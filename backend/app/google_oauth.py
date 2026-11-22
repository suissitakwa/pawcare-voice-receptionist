"""Owners connect their Google Calendar by signing in with Google once.

1. `start_url` sends the owner to Google's consent screen, asking to see their calendar list and
   to change a calendar's sharing. The `state` is encrypted, names the subscriber account and
   expires in 10 minutes, so a sign-in can't be finished by anyone else.
2. Google sends them back to the profile page with a code. `calendars_for` exchanges it for an
   access token and lists the calendars they own. The token goes back to the page only inside an
   encrypted ticket that expires in 10 minutes; it's never stored.
3. When they pick one, `share` adds HelloEver's service account to its sharing with "Make changes
   to events", the same as they would by hand. From then on the shared-account connection works
   as before, and Google's token is forgotten.
"""

import json
import secrets
import time
import urllib.parse
import uuid
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.google_calendar import API, CalendarError, Send, http_send, shared_account_email

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a secret
SCOPES = (
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.acls",
)
TTL_SECONDS = 600

# Replaced in tests.
transport: Send = http_send
clock = time.time


class SignInProblem(Exception):
    """Why signing in with Google or sharing the calendar didn't work, in words for the owner."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class OwnedCalendar:
    id: str
    name: str
    primary: bool


def available(settings: Settings) -> bool:
    """Signing in with Google needs an OAuth client, the shared service account, a place to
    come back to and the key that seals the state and ticket."""
    return bool(
        settings.google_oauth_client_id
        and settings.google_oauth_client_secret
        and settings.public_site_url
        and settings.integrations_key
        and shared_account_email(settings)
    )


def redirect_uri(settings: Settings) -> str:
    return settings.public_site_url.rstrip("/") + "/get-started"


def not_offered() -> SignInProblem:
    return SignInProblem(
        "google_sign_in_not_offered",
        "Signing in with Google isn't available yet. Share your calendar by hand instead.",
        503,
    )


def expired() -> SignInProblem:
    return SignInProblem(
        "google_sign_in_expired", "That Google sign-in has expired. Please connect again."
    )


def unreachable() -> SignInProblem:
    return SignInProblem(
        "calendar_unreachable", "Google couldn't be reached. Try again in a minute.", 503
    )


def seal(settings: Settings, kind: str, account_id: uuid.UUID, **values) -> str:
    body = json.dumps({"kind": kind, "account": str(account_id), **values}).encode()
    return Fernet(settings.integrations_key.encode()).encrypt_at_time(body, int(clock())).decode()


def unseal(settings: Settings, token: str, kind: str, account_id: uuid.UUID) -> dict:
    try:
        raw = Fernet(settings.integrations_key.encode()).decrypt_at_time(
            token.encode(), TTL_SECONDS, int(clock())
        )
        data = json.loads(raw)
    except (InvalidToken, ValueError):
        raise expired() from None
    if data.get("kind") != kind or data.get("account") != str(account_id):
        raise expired()
    return data


def start_url(settings: Settings, account_id: uuid.UUID, login_hint: str) -> str:
    if not available(settings):
        raise not_offered()
    state = seal(settings, "state", account_id, nonce=secrets.token_urlsafe(8))
    query = urllib.parse.urlencode(
        {
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": redirect_uri(settings),
            "response_type": "code",
            "scope": " ".join(SCOPES),
            # Only while they pick a calendar: no refresh token is asked for or kept.
            "access_type": "online",
            "include_granted_scopes": "true",
            "prompt": "select_account",
            "login_hint": login_hint,
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def call(method: str, url: str, token: str, payload: dict | None = None):
    headers = {"Authorization": f"Bearer {token}"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    try:
        return transport(method, url, body, headers)
    except CalendarError:
        raise unreachable() from None


def calendars_for(
    settings: Settings, account_id: uuid.UUID, code: str, state: str
) -> tuple[str, list[OwnedCalendar]]:
    """The calendars the owner can share, and a ticket for sharing one of them."""
    if not available(settings):
        raise not_offered()
    unseal(settings, state, "state", account_id)
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": redirect_uri(settings),
            "grant_type": "authorization_code",
        }
    ).encode()
    try:
        status, data = transport(
            "POST", TOKEN_URL, body, {"Content-Type": "application/x-www-form-urlencoded"}
        )
    except CalendarError:
        raise unreachable() from None
    if status != 200 or not data or "access_token" not in data:
        raise SignInProblem(
            "google_sign_in_failed", "Google didn't confirm the sign-in. Please connect again."
        )
    if not set(SCOPES) <= set(str(data.get("scope", "")).split()):
        raise SignInProblem(
            "google_permission_missing",
            "Please allow both permissions Google asks for: seeing your calendars and "
            "sharing the one you choose.",
        )
    token = data["access_token"]
    # Only calendars they own can be shared by them.
    status, listed = call("GET", f"{API}/users/me/calendarList?minAccessRole=owner", token)
    if status != 200 or listed is None:
        raise SignInProblem(
            "google_sign_in_failed", "Your calendars couldn't be read. Please connect again."
        )
    calendars = [
        OwnedCalendar(
            id=item["id"],
            name=item.get("summaryOverride") or item.get("summary") or item["id"],
            primary=bool(item.get("primary")),
        )
        for item in listed.get("items", [])
        if item.get("id") and not item.get("deleted")
    ]
    calendars.sort(key=lambda c: (not c.primary, c.name.lower()))
    return seal(settings, "ticket", account_id, token=token), calendars


def share(settings: Settings, account_id: uuid.UUID, ticket: str, calendar_id: str) -> None:
    """Shares the calendar with HelloEver's service account, "Make changes to events"."""
    if not available(settings):
        raise not_offered()
    token = unseal(settings, ticket, "ticket", account_id)["token"]
    email = shared_account_email(settings)
    path = "/calendars/" + urllib.parse.quote(calendar_id, safe="") + "/acl?sendNotifications=false"
    status, _ = call(
        "POST", API + path, token, {"role": "writer", "scope": {"type": "user", "value": email}}
    )
    if status in (200, 201):
        return
    if status == 403:
        raise SignInProblem(
            "calendar_sharing_blocked",
            "Google didn't let us share that calendar. Your Google Workspace may block sharing "
            "outside your organisation: ask your admin, or choose a personal calendar.",
        )
    if status == 404:
        raise SignInProblem(
            "calendar_not_found", "That calendar wasn't found in your Google account."
        )
    raise unreachable()
