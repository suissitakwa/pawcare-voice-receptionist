import uuid
from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
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


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="booking_end_after_start"),
        CheckConstraint("occupied_until >= ends_at", name="booking_buffer_after_end"),
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="booking_status_known"),
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
