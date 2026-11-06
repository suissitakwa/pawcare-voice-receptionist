"""Request and response shapes for subscriber accounts and business profiles."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.schemas import EMAIL_PATTERN, PHONE_PATTERN

# Pet grooming and hair businesses first. Adding a type means extending this list and the
# database check in migration 0008.
BusinessType = Literal["pet_grooming", "hair_salon", "barbershop"]
BUSINESS_TYPE_LABELS = {
    "pet_grooming": "Pet grooming",
    "hair_salon": "Hair salon",
    "barbershop": "Barbershop",
}
# One voice that sounds female and one that sounds male, from the OpenAI Realtime voices.
# More can be offered later; stored values outside this list are dropped on load.
VOICES = {"marin": ("Female voice", "female"), "cedar": ("Male voice", "male")}
Voice = Literal["marin", "cedar"]
CalendarProvider = Literal[
    "google", "outlook", "apple", "square", "moego", "vagaro", "fresha", "other", "none"
]
CALENDAR_LABELS = {
    "google": "Google Calendar",
    "outlook": "Outlook / Microsoft 365",
    "apple": "Apple Calendar",
    "square": "Square Appointments",
    "moego": "MoeGo",
    "vagaro": "Vagaro",
    "fresha": "Fresha",
    "other": "Another system",
    "none": "No calendar yet (use the receptionist's own)",
}
URL_PATTERN = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE)
MIN_PASSWORD_LENGTH = 12

ShortText = Annotated[str, Field(min_length=1, max_length=120)]


def clean_phone(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not PHONE_PATTERN.match(value) or sum(c.isdigit() for c in value) < 7:
        raise ValueError("Enter a phone number with at least 7 digits.")
    return value


def clean_email(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if not EMAIL_PATTERN.match(value):
        raise ValueError("Enter a valid email address.")
    return value


def clean_url(value: str) -> str:
    value = value.strip()
    if len(value) > 300 or not URL_PATTERN.match(value):
        raise ValueError("Enter a full web address starting with http:// or https://.")
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SignUpRequest(Strict):
    full_name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=254)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)
    accept_terms: bool

    _email = field_validator("email")(clean_email)

    @field_validator("accept_terms")
    @classmethod
    def terms_accepted(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Accept the terms to create an account.")
        return value


class SignInRequest(Strict):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=200)


class AccountPublic(BaseModel):
    email: str
    full_name: str


class SessionResult(BaseModel):
    token: str
    expires_at: datetime
    account: AccountPublic


class FaqItem(Strict):
    question: str = Field(min_length=3, max_length=200)
    answer: str = Field(min_length=1, max_length=1000)


class ProfileDetails(Strict):
    """Everything about the business beyond its name and type. Every field is optional while
    the profile is a draft; submitting checks the required ones."""

    business_phone: str | None = None
    website_url: str | None = None
    calendar_provider: CalendarProvider | None = None
    calendar_other: str | None = Field(default=None, max_length=60)
    knowledge_urls: list[str] = Field(default_factory=list, max_length=10)
    faqs: list[FaqItem] = Field(default_factory=list, max_length=100)
    voice: Voice | None = None
    ai_disclosure_ack: bool = False

    _phone = field_validator("business_phone")(clean_phone)

    @field_validator("website_url")
    @classmethod
    def website(cls, value: str | None) -> str | None:
        return clean_url(value) if value else None

    @field_validator("knowledge_urls")
    @classmethod
    def urls(cls, value: list[str]) -> list[str]:
        cleaned = [clean_url(url) for url in value]
        return list(dict.fromkeys(cleaned))

    @classmethod
    def from_stored(cls, stored: dict) -> "ProfileDetails":
        """Loads saved details, dropping fields this version no longer has or no longer
        accepts, so a profile saved by an earlier version still opens."""
        known = {k: v for k, v in stored.items() if k in cls.model_fields}
        for _ in range(len(known) + 1):
            try:
                return cls.model_validate(known)
            except ValidationError as error:
                bad = {str(e["loc"][0]) for e in error.errors() if e["loc"]}
                known = {k: v for k, v in known.items() if k not in bad}
        return cls()


class ProfileUpdate(Strict):
    """A partial update. Only the fields sent are changed."""

    business_name: ShortText | None = None
    business_type: BusinessType | None = None
    details: dict | None = None


class DocumentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class ProfilePublic(BaseModel):
    status: Literal["draft", "submitted"]
    business_name: str | None
    business_type: BusinessType | None
    details: ProfileDetails
    documents: list[DocumentPublic]
    missing: list[str]
    submitted_at: datetime | None
    updated_at: datetime


class Option(BaseModel):
    value: str
    label: str


class VoiceOption(Option):
    sounds: Literal["female", "male"]


class ProfileOptions(BaseModel):
    business_types: list[Option]
    voices: list[VoiceOption]
    calendar_providers: list[Option]
    document_types: list[str]
    max_document_bytes: int
