"""Admins see every sign-up and can correct, unlock, sign out, re-plan or delete it."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.admin import upsert_admin
from app.models import (
    AdminAction,
    AdminUser,
    Booking,
    Business,
    BusinessPhoneNumber,
    BusinessProfile,
    Groomer,
    Service,
    ServiceSizeRule,
    StaffUser,
    SubscriberAccount,
    SubscriberSession,
)
from app.provision import approve
from app.staff import hasher
from tests.test_subscribers import API, NOW, PASSWORD, auth, complete, fixed_clock  # noqa: F401

ADMIN = "/api/v1/admin"
ADMIN_EMAIL = "ops@example.com"
ADMIN_PASSWORD = "admin password long enough"


@pytest.fixture(autouse=True)
def clean(db: Session) -> Iterator[None]:
    def wipe() -> None:
        db.rollback()
        db.execute(delete(AdminAction))
        db.execute(delete(AdminUser))
        db.execute(delete(Booking).where(Booking.reference.like("PC-ADM%")))
        db.execute(delete(Business).where(Business.slug.like("shaggy-chic%")))
        db.commit()

    wipe()
    upsert_admin(db, ADMIN_EMAIL, "Ops", hasher.hash(ADMIN_PASSWORD))
    db.commit()
    yield
    wipe()


@pytest.fixture
def admin(client: TestClient) -> dict:
    response = client.post(
        f"{ADMIN}/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def account_id(db: Session, email: str = "sam@example.com"):
    return db.scalar(select(SubscriberAccount.id).where(SubscriberAccount.email == email))


def live_business(client: TestClient, db: Session) -> Business:
    """Sam's submitted profile, approved, with one booking and the demo line."""
    headers = auth(client)
    complete(client, headers)
    assert client.post(f"{API}/profile/submit", headers=headers).status_code == 200
    seed, _, _ = approve(db, "sam@example.com")
    business = db.scalar(select(Business).where(Business.slug == seed.slug))
    rule = db.scalar(
        select(ServiceSizeRule)
        .join(Service, Service.id == ServiceSizeRule.service_id)
        .where(Service.business_id == business.id)
        .limit(1)
    )
    groomer = db.scalar(select(Groomer).where(Groomer.business_id == business.id).limit(1))
    start = NOW + timedelta(days=3)
    db.add(
        Booking(
            business_id=business.id,
            groomer_id=groomer.id,
            service_id=rule.service_id,
            size_category_id=rule.size_category_id,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            occupied_until=start + timedelta(minutes=45),
            status="confirmed",
            reference="PC-ADM00001",
            price_cents=2000,
            owner_name="Pat",
            owner_email="pat@example.com",
            owner_phone=None,
            pet_name="Rex",
            summary={},
            idempotency_key="admin-test-booking",
            request_hash="0" * 64,
        )
    )
    db.add(BusinessPhoneNumber(business_id=business.id, number="+14155550199"))
    db.commit()
    return business


def test_every_route_needs_an_admin(client: TestClient) -> None:
    staff_or_owner = auth(client)
    for method, path in [
        ("GET", "/accounts"),
        ("GET", "/actions"),
        ("GET", "/me"),
        ("DELETE", "/accounts/00000000-0000-0000-0000-000000000000"),
    ]:
        assert client.request(method, f"{ADMIN}{path}").status_code == 401
        # A subscriber's own token is not an admin token.
        assert client.request(method, f"{ADMIN}{path}", headers=staff_or_owner).status_code == 401


def test_a_wrong_password_is_refused_and_locks_after_five(client: TestClient) -> None:
    for _ in range(5):
        wrong = client.post(f"{ADMIN}/login", json={"email": ADMIN_EMAIL, "password": "nope"})
        assert wrong.status_code == 401
    right = client.post(f"{ADMIN}/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert right.status_code == 429


def test_lists_and_searches_sign_ups(client: TestClient, admin: dict) -> None:
    auth(client)
    auth(client, email="jo@example.com", ip="203.0.113.9")
    listed = client.get(f"{ADMIN}/accounts", headers=admin).json()
    assert [a["email"] for a in listed] == ["jo@example.com", "sam@example.com"]
    assert listed[0]["profile_status"] == "draft"
    assert listed[0]["last_sign_in_at"] is not None
    found = client.get(f"{ADMIN}/accounts", params={"q": "JO@"}, headers=admin).json()
    assert [a["email"] for a in found] == ["jo@example.com"]


def test_updates_name_and_email_and_logs_it(client: TestClient, db: Session, admin: dict) -> None:
    auth(client)
    auth(client, email="jo@example.com", ip="203.0.113.9")
    target = account_id(db)
    taken = client.patch(
        f"{ADMIN}/accounts/{target}", json={"email": "jo@example.com"}, headers=admin
    )
    assert taken.status_code == 409
    changed = client.patch(
        f"{ADMIN}/accounts/{target}",
        json={"full_name": "Sam R.", "email": " Sam.Rivera@Example.com "},
        headers=admin,
    )
    assert changed.status_code == 200
    assert changed.json()["email"] == "sam.rivera@example.com"
    assert changed.json()["full_name"] == "Sam R."
    # The owner signs in with the new email.
    login = client.post(
        f"{API}/login", json={"email": "sam.rivera@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    log = client.get(f"{ADMIN}/actions", headers=admin).json()
    assert log[0]["action"] == "account.updated"
    assert log[0]["admin_email"] == ADMIN_EMAIL
    assert log[0]["details"]["email"] == ["sam@example.com", "sam.rivera@example.com"]


def test_unlocks_and_signs_out_everywhere(client: TestClient, db: Session, admin: dict) -> None:
    owner = auth(client)
    for _ in range(5):
        client.post(f"{API}/login", json={"email": "sam@example.com", "password": "wrong"})
    target = account_id(db)
    assert client.get(f"{ADMIN}/accounts/{target}", headers=admin).json()["locked"] is True

    unlocked = client.post(f"{ADMIN}/accounts/{target}/unlock", headers=admin).json()
    assert unlocked["locked"] is False
    assert (
        client.post(
            f"{API}/login", json={"email": "sam@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )

    client.post(f"{ADMIN}/accounts/{target}/sign-out", headers=admin)
    assert client.get(f"{API}/profile", headers=owner).status_code == 401
    db.expire_all()
    open_sessions = db.scalar(
        select(func.count())
        .select_from(SubscriberSession)
        .where(SubscriberSession.account_id == target, SubscriberSession.revoked_at.is_(None))
    )
    assert open_sessions == 0


def test_deleting_needs_the_email_typed(client: TestClient, db: Session, admin: dict) -> None:
    auth(client)
    target = account_id(db)
    refused = client.request(
        "DELETE",
        f"{ADMIN}/accounts/{target}",
        json={"confirm_email": "someone@example.com"},
        headers=admin,
    )
    assert refused.status_code == 422
    assert account_id(db) == target


def test_deletes_a_draft_sign_up(client: TestClient, db: Session, admin: dict) -> None:
    owner = auth(client)
    target = account_id(db)
    deleted = client.request(
        "DELETE",
        f"{ADMIN}/accounts/{target}",
        json={"confirm_email": "SAM@example.com"},
        headers=admin,
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted_business_slug"] is None
    db.expire_all()
    assert account_id(db) is None
    assert db.scalar(select(BusinessProfile).where(BusinessProfile.account_id == target)) is None
    assert client.get(f"{API}/profile", headers=owner).status_code == 401
    # The same email can sign up again.
    assert (
        client.post(
            f"{API}/signup",
            json={
                "full_name": "Sam",
                "email": "sam@example.com",
                "password": PASSWORD,
                "accept_terms": True,
            },
            headers={"X-Forwarded-For": "203.0.113.77"},
        ).status_code
        == 201
    )
    log = client.get(f"{ADMIN}/actions", headers=admin).json()
    assert log[0]["action"] == "account.deleted"
    assert log[0]["target"] == "sam@example.com"


def test_deletes_a_live_account_with_its_business(
    client: TestClient, db: Session, admin: dict
) -> None:
    business = live_business(client, db)
    slug, business_id = business.slug, business.id
    target = account_id(db)
    shown = client.get(f"{ADMIN}/accounts/{target}", headers=admin).json()
    assert shown["business_slug"] == slug
    assert shown["bookings"] == 1
    assert shown["phone_numbers"] == ["+14155550199"]
    assert [s["email"] for s in shown["staff_logins"]] == ["sam@example.com"]

    deleted = client.request(
        "DELETE",
        f"{ADMIN}/accounts/{target}",
        json={"confirm_email": "sam@example.com", "delete_business": True},
        headers=admin,
    ).json()
    assert deleted == {
        "deleted_email": "sam@example.com",
        "deleted_business_slug": slug,
        "deleted_bookings": 1,
        "released_phone_numbers": 1,
    }
    db.expire_all()
    assert db.get(Business, business_id) is None
    assert db.scalar(select(StaffUser).where(StaffUser.business_id == business_id)) is None
    assert client.get(f"/api/v1/b/{slug}/business").status_code == 404
    # The demo is untouched.
    assert db.scalar(select(Business).where(Business.slug == "pawcare-demo")) is not None


def test_can_keep_the_business_running(client: TestClient, db: Session, admin: dict) -> None:
    business = live_business(client, db)
    target = account_id(db)
    deleted = client.request(
        "DELETE",
        f"{ADMIN}/accounts/{target}",
        json={"confirm_email": "sam@example.com", "delete_business": False},
        headers=admin,
    ).json()
    assert deleted["deleted_business_slug"] is None
    db.expire_all()
    assert account_id(db) is None
    assert db.get(Business, business.id) is not None


def test_the_demo_business_is_never_deleted(client: TestClient, db: Session, admin: dict) -> None:
    auth(client)
    target = account_id(db)
    demo = db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))
    profile = db.scalar(select(BusinessProfile).where(BusinessProfile.account_id == target))
    profile.business_id = demo
    db.commit()
    refused = client.request(
        "DELETE",
        f"{ADMIN}/accounts/{target}",
        json={"confirm_email": "sam@example.com"},
        headers=admin,
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "business_protected"
    db.expire_all()
    assert account_id(db) == target


def test_changes_a_plan(client: TestClient, db: Session, admin: dict) -> None:
    business = live_business(client, db)
    changed = client.patch(
        f"{ADMIN}/businesses/{business.slug}/plan", json={"plan": "starter"}, headers=admin
    )
    assert changed.status_code == 200
    assert changed.json()["plan"] == "starter"
    extended = client.patch(
        f"{ADMIN}/businesses/{business.slug}/plan",
        json={"plan": "trial", "trial_days": 30},
        headers=admin,
    ).json()
    assert extended["plan"] == "trial"
    assert extended["trial_ends_at"].startswith((NOW + timedelta(days=30)).date().isoformat())
    assert (
        client.patch(
            f"{ADMIN}/businesses/nope/plan", json={"plan": "starter"}, headers=admin
        ).status_code
        == 404
    )
    log = client.get(f"{ADMIN}/actions", headers=admin).json()
    changes = [entry["details"]["plan"] for entry in log if entry["target"] == business.slug]
    assert sorted(changes) == [["starter", "trial"], ["trial", "starter"]]


def test_logout_ends_the_admin_session(client: TestClient, admin: dict) -> None:
    assert client.get(f"{ADMIN}/me", headers=admin).json()["email"] == ADMIN_EMAIL
    assert client.post(f"{ADMIN}/logout", headers=admin).status_code == 204
    assert client.get(f"{ADMIN}/me", headers=admin).status_code == 401
