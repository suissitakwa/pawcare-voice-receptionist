import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    database_url: str = "postgresql+psycopg://pawcare:pawcare@localhost:5440/pawcare"
    cors_allowed_origins: str = "http://localhost:5173"
    business_slug: str = "pawcare-demo"
    # Optional staff account created at startup. The password is supplied only as an
    # Argon2 hash, generated with `python -m app.staff hash`.
    staff_email: str = ""
    staff_display_name: str = "Staff"
    staff_password_hash: str = ""

    # Voice. "off" is the kill switch, "test" needs no provider and costs nothing,
    # and "live" calls OpenAI with the server-side key.
    voice_mode: Literal["off", "test", "live"] = "test"
    openai_api_key: str = Field(default="", repr=False)
    voice_model: str = "gpt-realtime-2.1-mini"
    voice_name: str = "marin"
    # Per business, per local day. A business's own limit (businesses.voice_daily_limit)
    # replaces it.
    voice_daily_session_limit: int = 30
    # Across every business, in the last 24 hours: the ceiling on browser voice cost.
    voice_daily_total_limit: int = 150
    voice_sessions_per_client_per_hour: int = 6
    voice_max_session_seconds: int = 300

    # Phone calls through OpenAI's SIP connection. Off until a number is connected. The
    # secret verifies OpenAI's webhook for incoming calls; voice_mode must be "live".
    phone_enabled: bool = False
    openai_webhook_secret: str = Field(default="", repr=False)
    phone_max_call_seconds: int = 600
    # Per business, per local day; businesses.phone_daily_limit replaces it.
    phone_daily_call_limit: int = 50
    # Across every business, in the last 24 hours: the ceiling on phone cost.
    phone_daily_total_limit: int = 250
    phone_calls_per_caller_per_hour: int = 4
    # The number callers dial, in E.164 form (+12605550123). Public: the website shows a
    # call button for it while the phone line is on.
    phone_number: str = ""
    # Phone overrides; empty means the same model and voice as the website. "low" eagerness
    # lets callers pause mid-sentence without being cut off.
    phone_model: str = ""
    phone_voice: str = ""
    phone_vad_eagerness: Literal["low", "medium", "high", "auto"] = "low"

    # Phone calls through Vapi, with its voices and turn-taking. Vapi asks this backend for
    # the assistant on every call and sends tool calls back here, signed with the secret.
    vapi_enabled: bool = False
    vapi_secret: str = Field(default="", repr=False)
    vapi_model: str = "gpt-4.1-mini"
    vapi_voice_provider: str = "11labs"
    # ElevenLabs "Rachel", a stock voice every account can use.
    vapi_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    # Used instead for a business whose owner chose the male voice: ElevenLabs "Adam".
    vapi_male_voice_id: str = "pNInz6obpgDQGcFmaJgB"
    vapi_transcriber_model: str = "nova-3"
    # Optional JSON merged into the assistant, to tune Vapi settings without a deploy.
    vapi_assistant_overrides: str = ""

    # Google Calendar, through a service account the calendar is shared with. The JSON key
    # may be pasted as is or base64-encoded. Both empty: no calendar.
    google_calendar_id: str = ""
    google_service_account_json: str = Field(default="", repr=False)
    # A Google OAuth client (web application) so owners connect their calendar by signing in:
    # they pick one of their calendars and it's shared with the service account above. Its
    # redirect URI is PUBLIC_SITE_URL + /get-started. Empty: owners share it by hand.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = Field(default="", repr=False)

    # Automation webhook, for example an n8n Webhook node. Events are signed with the
    # secret. Empty URL: events are kept for a while but not sent.
    webhook_url: str = ""
    webhook_secret: str = Field(default="", repr=False)
    # Off in tests, which deliver events explicitly.
    webhook_worker_enabled: bool = True

    # Encrypts each business's integration secrets (calendar keys, webhook secrets) in the
    # database. A Fernet key from `python -m app.integrations generate-key`. The settings above
    # still serve the deployment's own business when it has none stored.
    integrations_key: str = Field(default="", repr=False)

    # Owner emails about bookings and callback requests. With a Resend API key they go
    # through Resend's web API, which works where outgoing SMTP is blocked (Railway below
    # Pro); otherwise through the SMTP server below. Port 465 uses TLS from the start, any
    # other port STARTTLS. Neither, or no sender: no emails.
    resend_api_key: str = Field(default="", repr=False)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = Field(default="", repr=False)
    # The From address, for example "Receptionist <bookings@example.com>".
    email_from: str = ""
    # Customer texts (confirmations, changes, cancellations, reminders) through Twilio, for
    # businesses with texts switched on. They're sent from the business's own phone number,
    # or through the Messaging Service when one is set. No SID or token: no texts.
    twilio_account_sid: str = ""
    twilio_auth_token: str = Field(default="", repr=False)
    twilio_messaging_service_sid: str = ""
    # How long before an appointment its reminder is sent.
    reminder_hours_before: int = 24
    # The booking site's address, for links in emails, for example https://example.netlify.app
    public_site_url: str = ""
    # The service's own name and inbox: new sign-ups are emailed to ADMIN_EMAIL, and the
    # owner's receipt is signed with the name. Empty ADMIN_EMAIL: no sign-up notices.
    service_name: str = "HelloEver AI"
    # For trying onboarding end to end: every submitted profile goes live at once, with the
    # phone number given at sign-up ringing it. Off: every sign-up waits for review. Turn it
    # off before real customers sign up (docs/onboarding.md).
    auto_approve_signups: bool = False
    admin_email: str = ""

    # Suggested answers drafted from an owner's web pages and documents, for them to review.
    # Uses OPENAI_API_KEY; without one, suggestions are off. Each run reads one source.
    knowledge_model: str = "gpt-5-mini"
    knowledge_runs_per_hour: int = 40

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, value: str) -> str:
        # Managed hosts hand out postgres:// or postgresql:// URLs.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value.removeprefix(prefix)
        return value

    @field_validator("phone_number")
    @classmethod
    def e164_number(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"\+[1-9]\d{6,14}", value):
            raise ValueError("phone_number must be in E.164 form, for example +12605550123")
        return value

    @property
    def phone_model_name(self) -> str:
        return self.phone_model or self.voice_model

    @property
    def phone_voice_name(self) -> str:
        return self.phone_voice or self.voice_name

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
