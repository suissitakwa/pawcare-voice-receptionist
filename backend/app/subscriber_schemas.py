"""Request and response shapes for subscriber accounts and business profiles."""

import re
import uuid
from datetime import datetime, time
from typing import Annotated, Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas import EMAIL_PATTERN, PHONE_PATTERN

# Pet grooming and hair businesses first. Adding a type means extending this list and the
# database check in migration 0008.
BusinessType = Literal["pet_grooming", "hair_salon", "barbershop"]
BUSINESS_TYPE_LABELS = {
    "pet_grooming": "Pet grooming",
    "hair_salon": "Hair salon",
    "barbershop": "Barbershop",
}
# OpenAI Realtime voices offered to subscribers, with how they usually sound.
VOICES = {
    "marin": ("Marin", "female"),
    "coral": ("Coral", "female"),
    "shimmer": ("Shimmer", "female"),
    "cedar": ("Cedar", "male"),
    "ash": ("Ash", "male"),
    "echo": ("Echo", "male"),
}
Voice = Literal["marin", "coral", "shimmer", "cedar", "ash", "echo"]
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
Language = Literal["en", "es", "fr", "pt", "zh", "vi", "ar"]
LANGUAGE_LABELS = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "pt": "Portuguese",
    "zh": "Chinese",
    "vi": "Vietnamese",
    "ar": "Arabic",
}
Task = Literal["answer_questions", "book_appointments", "take_messages", "transfer_calls"]
TASK_LABELS = {
    "answer_questions": "Answer questions",
    "book_appointments": "Book appointments",
    "take_messages": "Take messages and callback requests",
    "transfer_calls": "Transfer calls to a person",
}
Plan = Literal["starter", "growth", "pro"]
PLAN_LABELS = {
    "starter": "Starter",
    "growth": "Growth",
    "pro": "Pro",
}
TIMEZONES = available_timezones()
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


class OpeningHours(Strict):
    # 0 is Monday, matching Python's date.weekday().
    weekday: int = Field(ge=0, le=6)
    opens: time
    closes: time

    @model_validator(mode="after")
    def closes_after_opens(self) -> "OpeningHours":
        if self.closes <= self.opens:
            raise ValueError("Closing time must be after opening time.")
        return self


class ServiceItem(Strict):
    name: str = Field(min_length=1, max_length=80)
    duration_minutes: int | None = Field(default=None, ge=5, le=600)
    price: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=300)


class FaqItem(Strict):
    question: str = Field(min_length=3, max_length=200)
    answer: str = Field(min_length=1, max_length=1000)


class ProfileDetails(Strict):
    """Everything about the business beyond its name and type. Every field is optional while
    the profile is a draft; submitting checks the required ones."""

    owner_phone: str | None = None
    website_url: str | None = None
    address: str | None = Field(default=None, max_length=200)
    timezone: str | None = None
    languages: list[Language] = Field(default_factory=list, max_length=7)

    business_phone: str | None = None
    phone_setup: Literal["forward_existing", "new_number", "undecided"] | None = None

    hours: list[OpeningHours] = Field(default_factory=list, max_length=7)
    calendar_provider: CalendarProvider | None = None
    calendar_other: str | None = Field(default=None, max_length=60)
    booking_notice_hours: int | None = Field(default=None, ge=0, le=168)

    services: list[ServiceItem] = Field(default_factory=list, max_length=50)

    knowledge_urls: list[str] = Field(default_factory=list, max_length=10)
    faqs: list[FaqItem] = Field(default_factory=list, max_length=100)
    policies: str | None = Field(default=None, max_length=3000)

    voice: Voice | None = None
    assistant_name: str | None = Field(default=None, max_length=40)
    greeting: str | None = Field(default=None, max_length=300)
    tone: Literal["friendly", "professional", "calm", "upbeat"] | None = None
    tasks: list[Task] = Field(default_factory=list, max_length=4)
    transfer_phone: str | None = None
    after_hours: Literal["take_message", "offer_booking"] | None = None
    avoid_topics: str | None = Field(default=None, max_length=1000)

    notify_email: str | None = None
    notify_sms: str | None = None

    plan: Plan | None = None
    ai_disclosure_ack: bool = False

    _phones = field_validator("owner_phone", "business_phone", "transfer_phone", "notify_sms")(
        clean_phone
    )
    _email = field_validator("notify_email")(clean_email)

    @field_validator("website_url")
    @classmethod
    def website(cls, value: str | None) -> str | None:
        return clean_url(value) if value else None

    @field_validator("knowledge_urls")
    @classmethod
    def urls(cls, value: list[str]) -> list[str]:
        cleaned = [clean_url(url) for url in value]
        return list(dict.fromkeys(cleaned))

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str | None) -> str | None:
        if value is not None and value not in TIMEZONES:
            raise ValueError("Choose a time zone from the list.")
        return value

    @field_validator("languages", "tasks")
    @classmethod
    def unique(cls, value: list) -> list:
        return list(dict.fromkeys(value))

    @field_validator("hours")
    @classmethod
    def one_range_per_day(cls, value: list[OpeningHours]) -> list[OpeningHours]:
        days = [h.weekday for h in value]
        if len(days) != len(set(days)):
            raise ValueError("Give each day at most one opening time.")
        return sorted(value, key=lambda h: h.weekday)


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
    languages: list[Option]
    tasks: list[Option]
    plans: list[Option]
    timezones: list[str]
    document_types: list[str]
    max_document_bytes: int
