"""Request and response shapes for subscriber accounts and business profiles."""

import re
import uuid
from datetime import datetime, time
from typing import Annotated, Literal
from zoneinfo import available_timezones

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

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


# Pet groomers price by dog size; other businesses have one price per service.
ServiceSize = Literal["small", "medium", "large", "any"]
SIZE_LABELS = {"small": "Small dog", "medium": "Medium dog", "large": "Large dog", "any": "Any"}
SIZES_BY_TYPE = {
    "pet_grooming": ["small", "medium", "large"],
    "hair_salon": ["any"],
    "barbershop": ["any"],
}


def prices_for(business_type: str | None, prices: list["ServicePrice"]) -> list["ServicePrice"]:
    """Only the price rows the form shows for this type of business. Rows left over from
    another type (dog sizes after switching to a salon) are dropped, not guessed from."""
    # No type chosen yet: the form shows dog sizes, as for pet grooming.
    sizes = SIZES_BY_TYPE.get(business_type or "pet_grooming", [])
    return [price for price in prices if price.size in sizes]


TIMEZONE_LABELS = {
    "America/Los_Angeles": "Pacific",
    "America/Denver": "Mountain",
    "America/Phoenix": "Arizona",
    "America/Chicago": "Central",
    "America/New_York": "Eastern",
    "America/Anchorage": "Alaska",
    "Pacific/Honolulu": "Hawaii",
}


class OpeningHours(Strict):
    weekday: int = Field(ge=0, le=6, description="0 is Monday")
    opens_at: time
    closes_at: time

    @model_validator(mode="after")
    def closes_after_opening(self) -> "OpeningHours":
        if self.closes_at <= self.opens_at:
            raise ValueError("Closing time must be after opening time.")
        return self


class ServicePrice(Strict):
    size: ServiceSize
    duration_minutes: int = Field(ge=5, le=480)
    price_cents: int = Field(ge=0, le=1_000_000)


class ServiceItem(Strict):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    prices: list[ServicePrice] = Field(default_factory=list, max_length=4)

    @field_validator("prices")
    @classmethod
    def one_price_per_size(cls, value: list[ServicePrice]) -> list[ServicePrice]:
        sizes = [p.size for p in value]
        if len(sizes) != len(set(sizes)):
            raise ValueError("Give each size one price.")
        return value


class TeamMember(Strict):
    name: str = Field(min_length=1, max_length=80)
    # Names of the services this person does. Empty: every service.
    services: list[str] = Field(default_factory=list, max_length=30)


class ProfileDetails(Strict):
    """Everything about the business beyond its name and type. Every field is optional while
    the profile is a draft; submitting checks the required ones."""

    business_phone: str | None = None
    website_url: str | None = None
    calendar_provider: CalendarProvider | None = None
    calendar_other: str | None = Field(default=None, max_length=60)
    # Set only by connecting (POST /profile/calendar), after HelloEver's account could make
    # changes to the calendar; a profile edit can't set them.
    google_calendar_id: str | None = Field(default=None, max_length=200)
    google_calendar_connected: bool = False
    # Its name in Google Calendar, shown to the owner instead of the id.
    google_calendar_name: str | None = Field(default=None, max_length=200)
    knowledge_urls: list[str] = Field(default_factory=list, max_length=10)
    faqs: list[FaqItem] = Field(default_factory=list, max_length=100)
    voice: Voice | None = None
    ai_disclosure_ack: bool = False
    timezone: str | None = None
    address: str | None = Field(default=None, max_length=200)
    hours: list[OpeningHours] = Field(default_factory=list, max_length=7)
    services: list[ServiceItem] = Field(default_factory=list, max_length=30)
    team: list[TeamMember] = Field(default_factory=list, max_length=20)
    # Mobile groomers only: they go to the customer, within these ZIP codes.
    is_mobile: bool = False
    service_zip_codes: list[str] = Field(default_factory=list, max_length=60)
    service_area: str | None = Field(default=None, max_length=200)

    @field_validator("service_zip_codes")
    @classmethod
    def five_digit_zips(cls, value: list[str]) -> list[str]:
        cleaned = [zip_code.strip() for zip_code in value if zip_code.strip()]
        for zip_code in cleaned:
            if not re.fullmatch(r"\d{5}", zip_code):
                raise ValueError(f"{zip_code} is not a five-digit ZIP code.")
        return sorted(set(cleaned))

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str | None) -> str | None:
        if value and value not in available_timezones():
            raise ValueError("Choose a time zone from the list.")
        return value or None

    @field_validator("hours")
    @classmethod
    def one_entry_per_day(cls, value: list[OpeningHours]) -> list[OpeningHours]:
        if len({h.weekday for h in value}) != len(value):
            raise ValueError("Give each day one set of hours.")
        return sorted(value, key=lambda h: h.weekday)

    @field_validator("services")
    @classmethod
    def distinct_services(cls, value: list[ServiceItem]) -> list[ServiceItem]:
        names = [s.name.strip().lower() for s in value]
        if len(names) != len(set(names)):
            raise ValueError("Give each service a different name.")
        return value

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
    # Set once the receptionist is live: the business's address, /b/{slug}.
    business_slug: str | None = None


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
    timezones: list[Option] = []
    # Price rows per business type: dog sizes for pet groomers, one row for the others.
    sizes: dict[str, list[Option]] = {}
    # The address a Google Calendar is shared with to connect it; None while not offered.
    calendar_share_email: str | None = None
    # Owners can sign in with Google to pick their calendar and share it in one go.
    google_sign_in: bool = False


class CalendarConnectRequest(Strict):
    calendar_id: str = Field(min_length=3, max_length=200)


class GoogleStart(BaseModel):
    url: str


class GoogleReturn(Strict):
    code: str = Field(min_length=1, max_length=2000)
    state: str = Field(min_length=1, max_length=2000)


class OwnedCalendarPublic(BaseModel):
    id: str
    name: str
    primary: bool


class GoogleCalendars(BaseModel):
    # Sealed for 10 minutes; sent back with the chosen calendar.
    ticket: str
    calendars: list[OwnedCalendarPublic]


class GoogleConnect(Strict):
    ticket: str = Field(min_length=1, max_length=4000)
    calendar_id: str = Field(min_length=3, max_length=200)


class CalendarCheck(BaseModel):
    """A live check that HelloEver can still read and change the connected calendar."""

    ok: bool
    code: str | None = None
    message: str | None = None
    # The calendar's name in Google Calendar, when it works.
    name: str | None = None
