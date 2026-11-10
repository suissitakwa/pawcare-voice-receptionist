import uuid
from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    tagline: Mapped[str] = mapped_column(String(200))
    about: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64))
    contact_email: Mapped[str] = mapped_column(String(254))
    contact_phone: Mapped[str] = mapped_column(String(32))
    address: Mapped[str] = mapped_column(String(200))
    is_demo: Mapped[bool] = mapped_column(server_default=true())
    slot_increment_minutes: Mapped[int] = mapped_column(server_default=text("15"))
    buffer_minutes: Mapped[int] = mapped_column(server_default=text("15"))
    min_notice_minutes: Mapped[int] = mapped_column(server_default=text("120"))
    booking_horizon_days: Mapped[int] = mapped_column(server_default=text("30"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    hours: Mapped[list["BusinessHours"]] = relationship(order_by="BusinessHours.weekday")


class SizeCategory(Base):
    __tablename__ = "size_categories"
    __table_args__ = (UniqueConstraint("business_id", "slug"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(200))
    requires_staff_assessment: Mapped[bool] = mapped_column(server_default=false())
    display_order: Mapped[int]


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("business_id", "slug"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default=true())
    display_order: Mapped[int]

    size_rules: Mapped[list["ServiceSizeRule"]] = relationship(back_populates="service")


class ServiceSizeRule(Base):
    __tablename__ = "service_size_rules"
    __table_args__ = (
        UniqueConstraint("service_id", "size_category_id"),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
        CheckConstraint("price_cents >= 0", name="price_not_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    size_category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("size_categories.id", ondelete="CASCADE")
    )
    duration_minutes: Mapped[int]
    price_cents: Mapped[int]

    service: Mapped[Service] = relationship(back_populates="size_rules")
    size_category: Mapped[SizeCategory] = relationship()


class BusinessHours(Base):
    __tablename__ = "business_hours"
    __table_args__ = (
        UniqueConstraint("business_id", "weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint("closes_at > opens_at", name="closes_after_opens"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    # 0 is Monday, matching Python's date.weekday().
    weekday: Mapped[int] = mapped_column(SmallInteger)
    opens_at: Mapped[time]
    closes_at: Mapped[time]


class Faq(Base):
    __tablename__ = "faqs"
    __table_args__ = (UniqueConstraint("business_id", "slug"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(String(200))
    answer: Mapped[str] = mapped_column(Text)
    display_order: Mapped[int]


class Groomer(Base):
    __tablename__ = "groomers"
    __table_args__ = (UniqueConstraint("business_id", "slug"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(server_default=true())


class GroomerHours(Base):
    __tablename__ = "groomer_hours"
    __table_args__ = (
        UniqueConstraint("groomer_id", "weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="groomer_hours_weekday_range"),
        CheckConstraint("ends_at > starts_at", name="groomer_hours_end_after_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    groomer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groomers.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(SmallInteger)
    starts_at: Mapped[time]
    ends_at: Mapped[time]


class GroomerBreak(Base):
    __tablename__ = "groomer_breaks"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="groomer_breaks_weekday_range"),
        CheckConstraint("ends_at > starts_at", name="groomer_breaks_end_after_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    groomer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groomers.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(SmallInteger)
    starts_at: Mapped[time]
    ends_at: Mapped[time]


class BlockedPeriod(Base):
    __tablename__ = "blocked_periods"
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="blocked_end_after_start"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    groomer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groomers.id", ondelete="CASCADE"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(200), server_default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="booking_end_after_start"),
        CheckConstraint("occupied_until >= ends_at", name="booking_buffer_after_end"),
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="booking_status_known"),
        CheckConstraint(
            "owner_email IS NOT NULL OR owner_phone IS NOT NULL", name="booking_has_contact"
        ),
        UniqueConstraint("reference", name="uq_bookings_reference"),
        UniqueConstraint("business_id", "idempotency_key", name="uq_bookings_idempotency_key"),
        UniqueConstraint("draft_id", name="uq_bookings_draft_id"),
        # bookings_no_overlap, the exclusion constraint, is created in migration 0004.
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    groomer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groomers.id", ondelete="RESTRICT"))
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"))
    size_category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("size_categories.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occupied_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reference: Mapped[str] = mapped_column(String(16))
    price_cents: Mapped[int]
    owner_name: Mapped[str] = mapped_column(String(80))
    owner_email: Mapped[str | None] = mapped_column(String(254))
    owner_phone: Mapped[str | None] = mapped_column(String(20))
    pet_name: Mapped[str] = mapped_column(String(40))
    summary: Mapped[dict] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("booking_drafts.id", ondelete="SET NULL")
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )
    voice_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("voice_sessions.id", ondelete="SET NULL")
    )


class BookingDraft(Base):
    """A prepared booking. Its details never change, so a confirmation binds to exactly them."""

    __tablename__ = "booking_drafts"
    __table_args__ = (
        CheckConstraint(
            "owner_email IS NOT NULL OR owner_phone IS NOT NULL", name="draft_has_contact"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    groomer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groomers.id"))
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"))
    size_category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("size_categories.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occupied_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price_cents: Mapped[int]
    owner_name: Mapped[str] = mapped_column(String(80))
    owner_email: Mapped[str | None] = mapped_column(String(254))
    owner_phone: Mapped[str | None] = mapped_column(String(20))
    pet_name: Mapped[str] = mapped_column(String(40))
    summary: Mapped[dict] = mapped_column(JSONB)
    summary_hash: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    voice_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("voice_sessions.id", ondelete="SET NULL")
    )


class StaffUser(Base):
    __tablename__ = "staff_users"
    __table_args__ = (
        UniqueConstraint("business_id", "email", name="uq_staff_users_business_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(254))
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    failed_attempts: Mapped[int] = mapped_column(server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StaffSession(Base):
    __tablename__ = "staff_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VoiceSession(Base):
    __tablename__ = "voice_sessions"
    __table_args__ = (
        CheckConstraint("mode IN ('test', 'live')", name="voice_mode_known"),
        CheckConstraint("status IN ('started', 'ended')", name="voice_status_known"),
        CheckConstraint("channel IN ('web', 'phone')", name="voice_channel_known"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(8))
    channel: Mapped[str] = mapped_column(String(8), server_default="web")
    model: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default="started")
    end_reason: Mapped[str | None] = mapped_column(String(32))
    client_hash: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tool_calls: Mapped[int] = mapped_column(server_default=text("0"))
    tool_failures: Mapped[int] = mapped_column(server_default=text("0"))
    booking_reference: Mapped[str | None] = mapped_column(String(16))


class CallbackRequest(Base):
    __tablename__ = "callback_requests"
    __table_args__ = (
        CheckConstraint("phone IS NOT NULL OR email IS NOT NULL", name="callback_has_contact"),
        CheckConstraint("status IN ('open', 'done')", name="callback_status_known"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    voice_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("voice_sessions.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    reason: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(300), server_default="")
    status: Mapped[str] = mapped_column(String(8), server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )


class SubscriberAccount(Base):
    """A business owner who signed up to configure their own receptionist."""

    __tablename__ = "subscriber_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(String(254), unique=True)
    full_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    failed_attempts: Mapped[int] = mapped_column(server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    client_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SubscriberSession(Base):
    __tablename__ = "subscriber_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriber_accounts.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BusinessProfile(Base):
    __tablename__ = "business_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriber_accounts.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(String(16), server_default="draft")
    business_name: Mapped[str | None] = mapped_column(String(120))
    business_type: Mapped[str | None] = mapped_column(String(32))
    details: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileDocument(Base):
    __tablename__ = "profile_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE")
    )
    filename: Mapped[str] = mapped_column(String(120))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookEvent(Base):
    """One event waiting for, or done with, delivery to the automation webhook."""

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))
