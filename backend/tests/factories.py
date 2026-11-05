from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Booking, Business, Groomer, Service, SizeCategory


def make_booking(
    db: Session,
    start: datetime,
    minutes: int = 30,
    status: str = "confirmed",
    buffer_minutes: int = 15,
    key: str | None = None,
) -> Booking:
    """Insert a booking directly, bypassing the API, as another customer or staff would."""
    business_id = db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))
    end = start + timedelta(minutes=minutes)
    booking = Booking(
        business_id=business_id,
        groomer_id=db.scalar(select(Groomer.id).where(Groomer.business_id == business_id)),
        service_id=db.scalar(select(Service.id).where(Service.slug == "nail-and-paw")),
        size_category_id=db.scalar(select(SizeCategory.id).where(SizeCategory.slug == "small")),
        starts_at=start,
        ends_at=end,
        occupied_until=end + timedelta(minutes=buffer_minutes),
        status=status,
        reference=f"PC-T{start:%H%M}{status[:1].upper()}{abs(hash(key or start)) % 1000:03d}",
        price_cents=2000,
        owner_name="Another Customer",
        owner_email="another@example.com",
        owner_phone=None,
        pet_name="Rex",
        summary={},
        idempotency_key=key or f"factory-{start.isoformat()}-{status}",
        request_hash="0" * 64,
    )
    db.add(booking)
    db.commit()
    return booking
