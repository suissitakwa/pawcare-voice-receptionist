from datetime import date, datetime, time
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
