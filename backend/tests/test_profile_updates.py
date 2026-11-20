"""Changes to a submitted profile reach us as one email per editing session."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Business, BusinessProfile, WebhookEvent
from app.owner_email import send_due
from tests.test_owner_email import Outbox
from tests.test_signup_emails import ADMIN, settings
from tests.test_subscribers import API, NOW, PDF, auth, complete, fixed_clock  # noqa: F401


def submitted(client: TestClient, db: Session) -> dict:
    headers = auth(client)
    complete(client, headers)
    client.post(f"{API}/profile/submit", headers=headers)
    # The sign-up emails go out first, so only update notices are left.
    send_due(db, settings(), NOW, Outbox())
    return headers


def patch(client: TestClient, headers: dict, **body):
    response = client.patch(f"{API}/profile", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response


def updates(db: Session) -> list[WebhookEvent]:
    db.expire_all()
    return list(
        db.scalars(select(WebhookEvent).where(WebhookEvent.event_type == "profile.updated"))
    )


def test_edits_before_submitting_send_nothing(client, db) -> None:
    headers = auth(client)
    patch(client, headers, business_name="Shaggy Chic")
    patch(client, headers, details={"business_phone": "(415) 555-0199"})
    assert updates(db) == []


def test_a_burst_of_edits_becomes_one_email_after_a_quiet_spell(client, db) -> None:
    headers = submitted(client, db)
    patch(client, headers, details={"business_phone": "(415) 555-0199"})
    patch(client, headers, details={"business_phone": "(415) 555-0177"})
    patch(
        client,
        headers,
        details={"faqs": [{"question": "Do you groom cats?", "answer": "Yes, on Mondays."}]},
    )
    client.post(
        f"{API}/profile/documents?filename=prices.pdf",
        content=PDF,
        headers={**headers, "Content-Type": "application/pdf"},
    )
    assert len(updates(db)) == 1

    outbox = Outbox()
    assert send_due(db, settings(), NOW + timedelta(minutes=5), outbox) == 0
    assert send_due(db, settings(), NOW + timedelta(minutes=11), outbox) == 1
    [message] = outbox.sent
    assert message["To"] == ADMIN
    assert message["Subject"] == "Profile updated: Shaggy Chic"
    body = message.get_content()
    # Only the latest phone number, once.
    assert "Phone:\n  (415) 555-0177" in body and "0199" not in body
    assert "Answers to common questions:\n  Q: Do you groom cats? A: Yes, on Mondays." in body
    assert "Documents:\n  prices.pdf" in body
    assert "They aren't live yet." in body


def test_saving_the_same_values_sends_nothing(client, db) -> None:
    headers = submitted(client, db)
    patch(client, headers, business_name="Shaggy Chic")
    assert updates(db) == []


def test_after_an_email_the_next_edit_starts_a_new_one(client, db) -> None:
    headers = submitted(client, db)
    patch(client, headers, business_name="Shaggy Chic Grooming")
    send_due(db, settings(), NOW + timedelta(minutes=11), Outbox())
    patch(client, headers, details={"voice": "marin"})
    assert len(updates(db)) == 2


def test_a_live_business_is_a_reminder_to_update_its_file(client, db) -> None:
    headers = submitted(client, db)
    demo = db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))
    db.execute(update(BusinessProfile).values(business_id=demo))
    db.commit()
    patch(client, headers, details={"business_phone": "(415) 555-0177"})
    outbox = Outbox()
    send_due(db, settings(), NOW + timedelta(minutes=11), outbox)
    assert "live at /b/pawcare-demo and doesn't change by itself" in outbox.sent[0].get_content()


def test_a_profile_submitted_before_hours_existed_can_still_be_edited(client, db) -> None:
    headers = submitted(client, db)
    profile = db.scalar(select(BusinessProfile))
    db.execute(
        update(BusinessProfile).values(details={**profile.details, "hours": [], "services": []})
    )
    db.commit()
    patch(client, headers, details={"business_phone": "(415) 555-0177"})
    # But it can't lose what it has.
    emptied = client.patch(f"{API}/profile", headers=headers, json={"details": {"voice": None}})
    assert emptied.status_code == 422
