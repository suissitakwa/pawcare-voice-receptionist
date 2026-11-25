import re
import uuid
from datetime import date, datetime, time
from typing import Annotated, Literal
from zoneinfo import available_timezones

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.names import clean_name


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
    # Service slugs this person does. Empty: every service.
    services: list[str] = []


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
    # Where owner notices go; leave out to use contact_email.
    notify_email: str | None = None
    industry: Literal["pet_grooming", "hair_salon", "barbershop", "home_services"] = "pet_grooming"
    # A mobile business goes to the customer: bookings need an address in these ZIP codes.
    is_mobile: bool = False
    service_zip_codes: list[str] = []
    service_area: str = Field(default="", max_length=200)
    # The receptionist's voice: "marin" sounds female, "cedar" male. Leave out for the
    # deployment's default.
    voice: Literal["marin", "cedar"] | None = None

    @field_validator("service_zip_codes")
    @classmethod
    def five_digit_zips(cls, value: list[str]) -> list[str]:
        cleaned = [zip_code.strip() for zip_code in value if zip_code.strip()]
        for zip_code in cleaned:
            if not re.fullmatch(r"\d{5}", zip_code):
                raise ValueError(f"{zip_code!r} is not a five-digit ZIP code")
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def mobile_fits_industry(self) -> "BusinessSeed":
        # Groomers may go to the customer and home services always do; salons and
        # barbershops never ask an address.
        if self.industry == "home_services" and not self.is_mobile:
            raise ValueError("a home services business must be mobile")
        if self.is_mobile and self.industry not in ("pet_grooming", "home_services"):
            raise ValueError("only a pet grooming or home services business can be mobile")
        return self

    slot_increment_minutes: int = Field(gt=0)
    buffer_minutes: int = Field(ge=0)
    min_notice_minutes: int = Field(ge=0)
    booking_horizon_days: int = Field(gt=0)
    # Optional daily limits for this business; without them the deployment's defaults apply.
    voice_daily_limit: int | None = Field(default=None, ge=0)
    phone_daily_limit: int | None = Field(default=None, ge=0)
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
    # pet_grooming, hair_salon or barbershop: which fields the booking form asks for.
    industry: str = "pet_grooming"
    # A mobile business comes to the customer, within its service area.
    is_mobile: bool = False
    service_area: str = ""
    hours: list[HoursPublic]
    # The receptionist's phone number, only while the phone line can answer.
    assistant_phone: str | None = None
    # Customers who give a phone number get booking texts and a reminder, so the form says so.
    texts_customers: bool = False


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


class StaffMemberPublic(BaseModel):
    slug: str
    name: str
    # Service slugs this person does.
    services: list[str]


class CatalogPublic(BaseModel):
    size_categories: list[SizeCategoryPublic]
    services: list[ServicePublic]
    # Customers can ask for someone by name only when there is more than one.
    staff: list[StaffMemberPublic] = []


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


# A business slug as it appears in an address: lowercase letters, digits and inner hyphens.
SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^\+?[0-9 ().-]{7,20}$")


class PrepareBookingRequest(BaseModel):
    """Everything is untrusted input, whether typed into the form or passed by the assistant."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    service: str = Field(min_length=1, max_length=64)
    # Leave out at a business with one size (salons and barbershops).
    size: str | None = Field(default=None, min_length=1, max_length=32)
    starts_at: AwareDatetime
    owner_name: str = Field(min_length=1, max_length=80)
    owner_email: str | None = Field(default=None, max_length=254)
    owner_phone: str | None = Field(default=None, max_length=20)
    # Needed at a pet business only.
    pet_name: str = Field(default="", max_length=40)
    # Where the appointment happens: needed at a mobile business only.
    service_address: str | None = Field(default=None, max_length=200)
    # A staff member's slug, only when the customer asked for someone. Otherwise anyone free.
    staff: str | None = Field(default=None, max_length=64)

    # Names spelled out on a call ("T A K W A") are joined into one word.
    _names = field_validator("owner_name", "pet_name")(clean_name)

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
    # Who does the appointment, at a business with more than one staff member.
    staff: str | None = None
    # Where it happens, at a mobile business.
    service_address: str | None = None


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
    business_slug: str
    business_name: str
    business_is_demo: bool
    business_industry: str = "pet_grooming"


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
    source: Literal["form", "voice"]
    # Where it was booked: the booking form, a phone call, or the website's voice assistant.
    channel: Literal["form", "phone", "website"] = "form"
    # When the customer last changed it by phone or on the website.
    changed_at: datetime | None = None
    cancelled_by: Literal["staff", "customer"] | None = None
    staff: str
    service_address: str | None = None


class StaffBlock(BaseModel):
    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    local_start: datetime
    local_end: datetime
    reason: str
    # None: the whole business.
    staff: str | None


class TeamMember(BaseModel):
    slug: str
    name: str


class BlockRequest(BaseModel):
    """Wall-clock dates and times in the business time zone."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    start_date: date
    start_time: time
    end_date: date
    end_time: time
    reason: str = Field(default="", max_length=200)
    # A staff member's slug; empty blocks the whole business.
    staff: str | None = Field(default=None, max_length=64)


class BlockCreated(BaseModel):
    block: StaffBlock
    conflicts: list[StaffBooking]


class StaffMetrics(BaseModel):
    upcoming_bookings: int
    bookings_last_7_days: int
    cancellations_last_7_days: int
    voice_bookings_last_7_days: int
    voice_sessions_last_7_days: int
    phone_calls_last_7_days: int
    open_callbacks: int
    tool_failures_last_7_days: int
    # Voice reply time over the last 7 days. None until a call has been measured.
    voice_replies_measured: int
    voice_reply_median_ms: int | None
    voice_reply_p90_ms: int | None


class StaffCallback(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone: str | None
    email: str | None
    reason: str
    note: str
    status: str
    created_at: datetime
    resolved_at: datetime | None


class StaffCall(BaseModel):
    """One conversation with the receptionist, by phone or on the website, for staff."""

    id: uuid.UUID
    started_at: datetime
    local_start: datetime
    channel: Literal["phone", "website"]
    # A scripted preview on the website, not a real conversation.
    test_mode: bool
    in_progress: bool
    duration_seconds: int | None
    ended_by: str | None
    booked_reference: str | None
    changed_reference: str | None
    cancelled_reference: str | None
    callback_requested: bool
    # Answered after the daily limit or plan ran out, so it could only take a message.
    message_only: bool
    tool_failures: int
    # Only when the provider reports one (Vapi).
    cost_usd: float | None


class VoiceSessionStarted(BaseModel):
    session_id: uuid.UUID
    session_token: str
    mode: Literal["test", "live"]
    client_secret: str | None
    expires_at: datetime | None
    max_duration_seconds: int


class VoiceUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(ge=0, le=10_000_000)
    output_tokens: int = Field(ge=0, le=10_000_000)
    responses: int = Field(ge=0, le=10_000)
    # Replies stopped by a limit or filter, and replies the caller talked over.
    incomplete_responses: int = Field(default=0, ge=0, le=10_000)
    interrupted_responses: int = Field(default=0, ge=0, le=10_000)
    # Caller turns with no words, such as background noise.
    empty_turns: int = Field(default=0, ge=0, le=10_000)
    # Milliseconds from the end of each caller turn to the start of the reply's audio,
    # measured in the browser.
    reply_latency_ms: list[Annotated[int, Field(ge=0, le=120_000)]] = Field(
        default_factory=list, max_length=200
    )


class PhoneEventReceived(BaseModel):
    status: Literal["answered", "declined", "ignored"]


class VoiceEndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=20, max_length=128)
    reason: Literal["user_ended", "timeout", "error", "disconnected", "mic_denied", "page_closed"]
    usage: VoiceUsage | None = None


class VoiceToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=20, max_length=128)
    # The raw JSON string the model produced. Parsed and validated by the backend.
    arguments: str = Field(default="", max_length=4000)


class PendingManage(BaseModel):
    """A change or cancellation waiting for the visitor's click on the website."""

    action: Literal["change", "cancel"]
    reference: str
    was: dict
    new: dict | None = None
    # Only the visitor's click sends it back. The model never sees it.
    confirm_token: str


class VoiceToolResult(BaseModel):
    ok: bool
    # Sent back to the model.
    result: dict
    # Shown on screen only. Holds the confirmation token, which the model never sees.
    prepared: PreparedBooking | None = None
    # Shown on screen only, like `prepared`.
    manage: PendingManage | None = None


class VoiceManageConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=20, max_length=128)
    confirm_token: str = Field(min_length=20, max_length=128)


class WebhookStatus(BaseModel):
    configured: bool
    problem: str | None
    pending: int
    delivered_last_7_days: int
    failed_last_7_days: int
    last_delivered_at: datetime | None
    last_error: str | None


class PlanOption(BaseModel):
    """A paid plan the owner can choose."""

    plan: str
    label: str
    price: str
    calls: int
    # Whether it can be bought by card here: Stripe is set up and the plan has its price.
    card_payment: bool


class PlanChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Literal["starter", "growth", "pro"] = "starter"


class PlanStatus(BaseModel):
    """The business's plan: a free trial, a paid plan, or unlimited (app/plans.py)."""

    plan: str
    # Starter, Growth or Pro; "Free trial" or "Unlimited" otherwise.
    label: str
    # trial, trial_ended, active, payment_stopped, allowance_used, or unlimited.
    state: str
    price: str
    # None for unlimited.
    calls_included: int | None
    calls_this_month: int
    trial_ends_at: datetime | None
    upgrade_requested_at: datetime | None
    # The card subscription's status in Stripe (active, past_due, canceled, ...), or None.
    billing_status: str | None = None
    # When a card subscription the owner cancelled stops; None while it renews.
    billing_ends_at: datetime | None = None
    # Whether the owner can pay by card here (Stripe is set up), or asks for an invoice.
    card_payment: bool = False
    # Whether the owner has a Stripe customer, so the billing portal can open.
    manage_billing: bool = False
    # Every paid plan, cheapest first.
    plan_options: list[PlanOption] = []


class BillingRedirect(BaseModel):
    """A Stripe page to send the owner to: Checkout or the customer portal."""

    url: str


class CalendarStatus(BaseModel):
    connected: bool
    calendar_id: str | None
    # Its name in Google Calendar, shown instead of the id when known.
    calendar_name: str | None = None
    # From a live check with Google: None when bookings can be read and written.
    problem_code: str | None
    problem: str | None
    waiting: int
    last_synced_at: datetime | None
    last_error: str | None


class EmailStatus(BaseModel):
    # Where owner emails go, or None when nothing would be sent.
    address: str | None
    # Whether the deployment can send email at all.
    enabled: bool
    sent_last_7_days: int
    last_error: str | None


class WebhookTestQueued(BaseModel):
    event_id: uuid.UUID
