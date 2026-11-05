import re
import uuid
from datetime import date, datetime, time
from zoneinfo import available_timezones

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class SeedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SizeCategorySeed(SeedModel):
    slug: str
    label: str
    description: str
    requires_staff_assessment: bool


class ServiceOptionSeed(SeedModel):
    size: str
    duration_minutes: int = Field(gt=0)
    price_cents: int = Field(ge=0)


class ServiceSeed(SeedModel):
    slug: str
    name: str
    description: str
    options: list[ServiceOptionSeed]


class HoursSeed(SeedModel):
    weekday: int = Field(ge=0, le=6)
    opens_at: time
    closes_at: time


class FaqSeed(SeedModel):
    slug: str
    question: str
    answer: str


class GroomerPeriodSeed(SeedModel):
    weekday: int = Field(ge=0, le=6)
    starts_at: time
    ends_at: time


class GroomerSeed(SeedModel):
    slug: str
    display_name: str
    hours: list[GroomerPeriodSeed]
    breaks: list[GroomerPeriodSeed]


class BusinessSeed(SeedModel):
    slug: str
    name: str
    tagline: str
    about: str
    timezone: str
    contact_email: str
    contact_phone: str
    address: str
    is_demo: bool
    slot_increment_minutes: int = Field(gt=0)
    buffer_minutes: int = Field(ge=0)
    min_notice_minutes: int = Field(ge=0)
    booking_horizon_days: int = Field(gt=0)
    hours: list[HoursSeed]
    groomers: list[GroomerSeed]
    size_categories: list[SizeCategorySeed]
    services: list[ServiceSeed]
    faqs: list[FaqSeed]

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        if value not in available_timezones():
            raise ValueError(f"unknown timezone: {value}")
        return value

    @model_validator(mode="after")
    def options_reference_known_sizes(self) -> "BusinessSeed":
        sizes = {size.slug: size for size in self.size_categories}
        for service in self.services:
            for option in service.options:
                size = sizes.get(option.size)
                if size is None:
                    raise ValueError(f"{service.slug}: unknown size {option.size}")
                if size.requires_staff_assessment:
                    raise ValueError(f"{service.slug}: {option.size} needs staff assessment")
        return self


class HoursPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    weekday: int
    opens_at: time
    closes_at: time


class BusinessPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    tagline: str
    about: str
    timezone: str
    contact_email: str
    contact_phone: str
    address: str
    is_demo: bool
    hours: list[HoursPublic]


class SizeCategoryPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    label: str
    description: str
    requires_staff_assessment: bool


class ServiceOptionPublic(BaseModel):
    size: str
    duration_minutes: int
    price_cents: int


class ServicePublic(BaseModel):
    slug: str
    name: str
    description: str
    options: list[ServiceOptionPublic]


class CatalogPublic(BaseModel):
    size_categories: list[SizeCategoryPublic]
    services: list[ServicePublic]


class FaqPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    question: str
    answer: str


class SlotPublic(BaseModel):
    starts_at: datetime
    ends_at: datetime
    local_start: datetime
    local_end: datetime


class AvailabilityPublic(BaseModel):
    date: date
    timezone: str
    service: str
    size: str
    duration_minutes: int
    closed: bool
    slots: list[SlotPublic]


class LiveStatus(BaseModel):
    status: str


class ReadyStatus(BaseModel):
    status: str
    database: str
    schema_revision: str | None = None


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^\+?[0-9 ().-]{7,20}$")


class PrepareBookingRequest(BaseModel):
    """Everything is untrusted input, whether typed into the form or passed by the assistant."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    service: str = Field(min_length=1, max_length=64)
    size: str = Field(min_length=1, max_length=32)
    starts_at: AwareDatetime
    owner_name: str = Field(min_length=1, max_length=80)
    owner_email: str | None = Field(default=None, max_length=254)
    owner_phone: str | None = Field(default=None, max_length=20)
    pet_name: str = Field(min_length=1, max_length=40)

    @field_validator("owner_email")
    @classmethod
    def valid_email(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if not EMAIL_PATTERN.match(value):
            raise ValueError("not a valid email address")
        return value.lower()

    @field_validator("owner_phone")
    @classmethod
    def valid_phone(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if not PHONE_PATTERN.match(value):
            raise ValueError("not a valid phone number")
        return value

    @model_validator(mode="after")
    def has_contact(self) -> "PrepareBookingRequest":
        if self.owner_email is None and self.owner_phone is None:
            raise ValueError("an email address or phone number is required")
        return self


class BookingSummary(BaseModel):
    service: str
    size: str
    pet_name: str
    owner_name: str
    owner_email: str | None
    owner_phone: str | None
    starts_at: datetime
    local_start: datetime
    local_end: datetime
    timezone: str
    duration_minutes: int
    price_cents: int


class PreparedBooking(BaseModel):
    confirmation_token: str
    summary_hash: str
    expires_at: datetime
    summary: BookingSummary


class ConfirmBookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_token: str = Field(min_length=20, max_length=128)
    summary_hash: str = Field(min_length=64, max_length=64)


class BookingConfirmation(BaseModel):
    reference: str
    status: str
    summary: BookingSummary


class StaffLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class StaffPublic(BaseModel):
    email: str
    display_name: str


class StaffLoginResult(BaseModel):
    token: str
    expires_at: datetime
    staff: StaffPublic


class StaffBooking(BaseModel):
    reference: str
    status: str
    service: str
    size: str
    pet_name: str
    owner_name: str
    owner_email: str | None
    owner_phone: str | None
    starts_at: datetime
    local_start: datetime
    local_end: datetime
    price_cents: int
    created_at: datetime
    cancelled_at: datetime | None


class StaffBlock(BaseModel):
    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    local_start: datetime
    local_end: datetime
    reason: str


class BlockRequest(BaseModel):
    """Wall-clock dates and times in the business time zone."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    start_date: date
    start_time: time
    end_date: date
    end_time: time
    reason: str = Field(default="", max_length=200)


class BlockCreated(BaseModel):
    block: StaffBlock
    conflicts: list[StaffBooking]


class StaffMetrics(BaseModel):
    upcoming_bookings: int
    bookings_last_7_days: int
    cancellations_last_7_days: int
