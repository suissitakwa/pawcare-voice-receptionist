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

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, value: str) -> str:
        # Managed hosts hand out postgres:// or postgresql:// URLs.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value.removeprefix(prefix)
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
