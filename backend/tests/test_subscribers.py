"""Subscriber accounts and the business profile that configures a receptionist."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app, get_now
from app.models import BusinessProfile, ProfileDocument, SubscriberAccount

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)
API = "/api/v1/subscribers"
PASSWORD = "correct horse battery"
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"

COMPLETE_DETAILS = {
    "business_phone": "(415) 555-0134",
    "timezone": "America/Los_Angeles",
    "hours": [{"weekday": 1, "opens": "09:00", "closes": "17:00"}],
    "services": [{"name": "Full groom", "duration_minutes": 120, "price": "from $90"}],
    "faqs": [{"question": "Do you groom cats?", "answer": "Dogs only."}],
    "voice": "cedar",
    "tasks": ["answer_questions", "book_appointments"],
    "calendar_provider": "google",
    "notify_email": "Owner@Example.com",
    "plan": "starter",
    "ai_disclosure_ack": True,
}


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def sign_up(client: TestClient, email: str = "sam@example.com", ip: str = "203.0.113.5"):
    return client.post(
        f"{API}/signup",
        json={
            "full_name": "Sam Rivera",
            "email": email,
            "password": PASSWORD,
            "accept_terms": True,
        },
        headers={"X-Forwarded-For": ip},
    )


def auth(client: TestClient, **kwargs) -> dict:
    return {"Authorization": f"Bearer {sign_up(client, **kwargs).json()['token']}"}


def complete(client: TestClient, headers: dict) -> None:
    response = client.patch(
        f"{API}/profile",
        headers=headers,
        json={
            "business_name": "Shaggy Chic",
            "business_type": "pet_grooming",
            "details": COMPLETE_DETAILS,
        },
    )
    assert response.status_code == 200, response.text


def test_options_offer_grooming_and_hair_businesses_and_both_voice_kinds(client) -> None:
    body = client.get(f"{API}/options").json()
    assert [t["value"] for t in body["business_types"]] == [
        "pet_grooming",
        "hair_salon",
        "barbershop",
    ]
    assert {v["sounds"] for v in body["voices"]} == {"female", "male"}
    assert "America/Los_Angeles" in body["timezones"]
    assert body["max_document_bytes"] == 5 * 1024 * 1024


def test_sign_up_creates_an_account_a_draft_profile_and_a_session(client, db: Session) -> None:
    response = sign_up(client, email="Sam@Example.com")
    assert response.status_code == 201
    body = response.json()
    assert body["account"] == {"email": "sam@example.com", "full_name": "Sam Rivera"}
    account = db.scalar(select(SubscriberAccount))
    assert account.password_hash.startswith("$argon2")
    assert PASSWORD not in account.password_hash
    assert "203.0.113.5" not in account.client_hash
    profile = client.get(f"{API}/profile", headers={"Authorization": f"Bearer {body['token']}"})
    assert profile.json()["status"] == "draft"
    assert "business_name" in profile.json()["missing"]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"password": "short"}, "password"),
        ({"email": "not-an-email"}, "email"),
        ({"accept_terms": False}, "accept_terms"),
    ],
)
def test_sign_up_rejects_weak_or_incomplete_requests(client, change, field) -> None:
    payload = {
        "full_name": "Sam",
        "email": "sam@example.com",
        "password": PASSWORD,
        "accept_terms": True,
        **change,
    }
    response = client.post(f"{API}/signup", json=payload)
    assert response.status_code == 422
    assert field in response.text


def test_one_account_per_email(client) -> None:
    assert sign_up(client).status_code == 201
    again = sign_up(client, email="SAM@example.com")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "email_taken"


def test_sign_ups_are_rate_limited_per_visitor(client) -> None:
    for n in range(5):
        assert sign_up(client, email=f"u{n}@example.com").status_code == 201
    limited = sign_up(client, email="u9@example.com")
    assert limited.status_code == 429
    assert sign_up(client, email="u9@example.com", ip="198.51.100.4").status_code == 201
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(hours=1, minutes=1)
    assert sign_up(client, email="u10@example.com").status_code == 201


def test_sign_in_sign_out_and_lockout(client) -> None:
    sign_up(client)
    good = {"email": "SAM@example.com", "password": PASSWORD}
    token = client.post(f"{API}/login", json=good).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(f"{API}/me", headers=headers).status_code == 200
    assert client.post(f"{API}/logout", headers=headers).status_code == 204
    assert client.get(f"{API}/me", headers=headers).status_code == 401

    bad = {"email": "sam@example.com", "password": "wrong password!"}
    for _ in range(5):
        assert client.post(f"{API}/login", json=bad).status_code == 401
    assert client.post(f"{API}/login", json=good).status_code == 429
    unknown = client.post(f"{API}/login", json={**good, "email": "nobody@example.com"})
    assert unknown.json()["error"]["code"] == "invalid_credentials"


def test_profile_needs_a_session(client) -> None:
    assert client.get(f"{API}/profile").status_code == 401
    assert client.patch(f"{API}/profile", json={}).status_code == 401


def test_saving_sections_merges_them_and_reports_what_is_missing(client) -> None:
    headers = auth(client)
    client.patch(f"{API}/profile", headers=headers, json={"business_name": "Fade Factory"})
    response = client.patch(
        f"{API}/profile",
        headers=headers,
        json={"business_type": "barbershop", "details": {"voice": "marin", "plan": "growth"}},
    )
    body = response.json()
    assert body["business_name"] == "Fade Factory"
    assert body["details"]["voice"] == "marin"
    later = client.patch(f"{API}/profile", headers=headers, json={"details": {"plan": "pro"}})
    assert later.json()["details"]["voice"] == "marin"
    assert later.json()["details"]["plan"] == "pro"
    assert "hours" in later.json()["missing"]
    assert "business_name" not in later.json()["missing"]


@pytest.mark.parametrize(
    ("details", "field"),
    [
        ({"business_phone": "call me"}, "business_phone"),
        ({"timezone": "Mars/Olympus"}, "timezone"),
        ({"hours": [{"weekday": 1, "opens": "17:00", "closes": "09:00"}]}, "hours"),
        (
            {
                "hours": [
                    {"weekday": 2, "opens": "09:00", "closes": "12:00"},
                    {"weekday": 2, "opens": "13:00", "closes": "17:00"},
                ]
            },
            "hours",
        ),
        ({"knowledge_urls": ["javascript:alert(1)"]}, "knowledge_urls"),
        ({"voice": "robot"}, "voice"),
        ({"unknown_field": 1}, "unknown_field"),
    ],
)
def test_invalid_details_are_rejected_by_field(client, details, field) -> None:
    response = client.patch(f"{API}/profile", headers=auth(client), json={"details": details})
    assert response.status_code == 422
    assert field in response.json()["error"]["message"]


def test_only_the_supported_business_types_are_accepted(client) -> None:
    response = client.patch(
        f"{API}/profile", headers=auth(client), json={"business_type": "plumbing"}
    )
    assert response.status_code == 422


def test_submit_requires_every_section_then_marks_it_submitted(client, db: Session) -> None:
    headers = auth(client)
    early = client.post(f"{API}/profile/submit", headers=headers)
    assert early.status_code == 422
    assert "business_phone" in early.json()["error"]["message"]
    complete(client, headers)
    response = client.post(f"{API}/profile/submit", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "submitted"
    assert body["missing"] == []
    assert body["details"]["notify_email"] == "owner@example.com"
    assert db.scalar(select(BusinessProfile.status)) == "submitted"


def test_transferring_calls_needs_a_number_to_transfer_to(client) -> None:
    headers = auth(client)
    complete(client, headers)
    tasks = {"details": {"tasks": ["transfer_calls"]}}
    body = client.patch(f"{API}/profile", headers=headers, json=tasks).json()
    assert "transfer_phone" in body["missing"]


def test_a_submitted_profile_cannot_be_made_incomplete(client) -> None:
    headers = auth(client)
    complete(client, headers)
    client.post(f"{API}/profile/submit", headers=headers)
    response = client.patch(f"{API}/profile", headers=headers, json={"details": {"hours": []}})
    assert response.status_code == 422
    still = client.get(f"{API}/profile", headers=headers).json()
    assert len(still["details"]["hours"]) == 1


def test_each_account_sees_only_its_own_profile(client) -> None:
    first = auth(client, email="a@example.com")
    second = auth(client, email="b@example.com")
    client.patch(f"{API}/profile", headers=first, json={"business_name": "Snip"})
    assert client.get(f"{API}/profile", headers=second).json()["business_name"] is None


def upload(client, headers, content=PDF, kind="application/pdf", name="price list.pdf"):
    return client.post(
        f"{API}/profile/documents",
        params={"filename": name},
        content=content,
        headers={**headers, "Content-Type": kind},
    )


def test_documents_are_stored_once_and_count_as_knowledge(client, db: Session) -> None:
    headers = auth(client)
    first = upload(client, headers)
    assert first.status_code == 201
    assert first.json()["filename"] == "price list.pdf"
    assert upload(client, headers).json()["id"] == first.json()["id"]
    assert db.scalar(select(ProfileDocument.size_bytes)) == len(PDF)
    profile = client.get(f"{API}/profile", headers=headers).json()
    assert "knowledge" not in profile["missing"]
    assert [d["filename"] for d in profile["documents"]] == ["price list.pdf"]


@pytest.mark.parametrize(
    ("content", "kind", "status"),
    [
        (b"MZ\x90\x00 not a pdf", "application/pdf", 415),
        (b"<script>", "text/html", 415),
        (b"\xff\xfe\x00bad", "text/plain", 415),
        (b"", "text/plain", 422),
        (b"%PDF-" + b"0" * (5 * 1024 * 1024), "application/pdf", 413),
    ],
)
def test_unsafe_or_oversized_documents_are_refused(client, content, kind, status) -> None:
    assert upload(client, auth(client), content=content, kind=kind).status_code == status


def test_file_names_cannot_carry_paths(client) -> None:
    response = upload(client, auth(client), name="../../etc/passwd.txt", kind="text/plain")
    assert response.json()["filename"] == "passwd.txt"


def test_documents_can_be_deleted_only_by_their_owner(client) -> None:
    owner = auth(client, email="a@example.com")
    other = auth(client, email="b@example.com")
    document = upload(client, owner).json()
    assert (
        client.delete(f"{API}/profile/documents/{document['id']}", headers=other).status_code == 404
    )
    assert (
        client.delete(f"{API}/profile/documents/{document['id']}", headers=owner).status_code == 204
    )
