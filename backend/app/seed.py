"""Load demo data. Safe to re-run: records are upserted by slug."""

import json
import uuid
from pathlib import Path

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import get_engine
from app.models import (
    Business,
    BusinessHours,
    Faq,
    Groomer,
    GroomerBreak,
    GroomerHours,
    Service,
    ServiceSizeRule,
    SizeCategory,
)
from app.schemas import BusinessSeed

SEED_FILE = Path(__file__).parent / "seed_data" / "demo_business.json"


def seed_business(session: Session) -> None:
    seed = BusinessSeed.model_validate(json.loads(SEED_FILE.read_text()))
    profile = seed.model_dump(exclude={"hours", "groomers", "size_categories", "services", "faqs"})
    updates = {key: value for key, value in profile.items() if key != "slug"}
    business_id = session.scalar(
        insert(Business)
        .values(**profile)
        .on_conflict_do_update(
            index_elements=[Business.slug], set_={**updates, "updated_at": func.now()}
        )
        .returning(Business.id)
    )

    # Hours and FAQs have nothing pointing at them, so the seed file simply replaces them.
    session.execute(delete(BusinessHours).where(BusinessHours.business_id == business_id))
    session.add_all(
        BusinessHours(business_id=business_id, **hours.model_dump()) for hours in seed.hours
    )
    session.execute(delete(Faq).where(Faq.business_id == business_id))
    session.add_all(
        Faq(business_id=business_id, display_order=order, **faq.model_dump())
        for order, faq in enumerate(seed.faqs)
    )

    for groomer in seed.groomers:
        groomer_id = _upsert(
            session,
            Groomer,
            business_id,
            groomer.slug,
            {"display_name": groomer.display_name, "is_active": True},
        )
        session.execute(delete(GroomerHours).where(GroomerHours.groomer_id == groomer_id))
        session.add_all(
            GroomerHours(groomer_id=groomer_id, **period.model_dump()) for period in groomer.hours
        )
        session.execute(delete(GroomerBreak).where(GroomerBreak.groomer_id == groomer_id))
        session.add_all(
            GroomerBreak(groomer_id=groomer_id, **period.model_dump()) for period in groomer.breaks
        )

    # Services and sizes keep their ids so that future bookings can reference them.
    size_ids = {
        size.slug: _upsert(
            session,
            SizeCategory,
            business_id,
            size.slug,
            {**size.model_dump(exclude={"slug"}), "display_order": order},
        )
        for order, size in enumerate(seed.size_categories)
    }
    service_ids = []
    for order, service in enumerate(seed.services):
        service_id = _upsert(
            session,
            Service,
            business_id,
            service.slug,
            {
                "name": service.name,
                "description": service.description,
                "is_active": True,
                "display_order": order,
            },
        )
        service_ids.append(service_id)
        session.execute(delete(ServiceSizeRule).where(ServiceSizeRule.service_id == service_id))
        session.add_all(
            ServiceSizeRule(
                service_id=service_id,
                size_category_id=size_ids[option.size],
                duration_minutes=option.duration_minutes,
                price_cents=option.price_cents,
            )
            for option in service.options
        )
    session.execute(
        update(Service)
        .where(Service.business_id == business_id, Service.id.not_in(service_ids))
        .values(is_active=False)
    )
    session.flush()


def _upsert(
    session: Session,
    model: type[Service | SizeCategory | Groomer],
    business_id,
    slug: str,
    values: dict,
) -> uuid.UUID:
    return session.scalar(
        insert(model)
        .values(business_id=business_id, slug=slug, **values)
        .on_conflict_do_update(index_elements=[model.business_id, model.slug], set_=values)
        .returning(model.id)
    )


def main() -> None:
    with Session(get_engine()) as session, session.begin():
        seed_business(session)
    print("Demo data seeded.")


if __name__ == "__main__":
    main()
