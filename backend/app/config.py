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
    voice_daily_session_limit: int = 30
    voice_sessions_per_client_per_hour: int = 6
    voice_max_session_seconds: int = 300

    # Phone calls through OpenAI's SIP connection. Off until a number is connected. The
    # secret verifies OpenAI's webhook for incoming calls; voice_mode must be "live".
    phone_enabled: bool = False
    openai_webhook_secret: str = Field(default="", repr=False)
    phone_max_call_seconds: int = 600
    phone_daily_call_limit: int = 50
    phone_calls_per_caller_per_hour: int = 4
    # The number callers dial, in E.164 form (+12605550123). Public: the website shows a
    # call button for it while the phone line is on.
    phone_number: str = ""
    # Phone overrides; empty means the same model and voice as the website. "low" eagerness
    # lets callers pause mid-sentence without being cut off.
    phone_model: str = ""
    phone_voice: str = ""
    phone_vad_eagerness: Literal["low", "medium", "high", "auto"] = "low"

    # Google Calendar, through a service account the calendar is shared with. The JSON key
    # may be pasted as is or base64-encoded. Both empty: no calendar.
    google_calendar_id: str = ""
    google_service_account_json: str = Field(default="", repr=False)

    # Automation webhook, for example an n8n Webhook node. Events are signed with the
    # secret. Empty URL: events are kept for a while but not sent.
    webhook_url: str = ""
    webhook_secret: str = Field(default="", repr=False)
    # Off in tests, which deliver events explicitly.
    webhook_worker_enabled: bool = True

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
