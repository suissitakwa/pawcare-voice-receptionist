"""Filling in the profile from the owner's website."""

import json
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import knowledge, website_details
from app.config import Settings, get_settings
from app.knowledge import KnowledgeError, Page
from app.main import app, get_now
from app.models import KnowledgeRun
from tests.test_knowledge import FakeWeb

API = "/api/v1/subscribers"
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

HOME = (
    b"<html><body><h1>Shaggy Chic Grooming</h1>"
    b"<p>Call us at (408) 555-0134. Visit 12 Market St, San Jose, CA 95113.</p>"
    b'<nav><a href="/services">Services &amp; prices</a> <a href="/contact">Contact</a>'
    b' <a href="/logo.png">Logo</a> <a href="https://instagram.example/shaggy">Instagram</a>'
    b' <a href="/blog/puppy-tips">Blog</a></nav></body></html>'
)
SERVICES = (
    b"<html><body><h2>Services</h2>"
    b"<p>Full Groom: bath, haircut and nails. Small dogs $65, 90 minutes. Large dogs $105.</p>"
    b"<p>Nail Trim $15</p><h2>Hours</h2>"
    b"<p>Tuesday to Saturday 9am to 5pm. Closed Sunday and Monday.</p>"
    b"<p>Your groomers: Sam and Jo.</p></body></html>"
)
HTML = {"Content-Type": "text/html; charset=utf-8"}


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


@pytest.fixture
def site(monkeypatch) -> FakeWeb:
    fake = FakeWeb(
        names={"shaggy.example": ["93.184.216.34"], "www.shaggy.example": ["93.184.216.34"]},
        pages={
            "https://shaggy.example/": (200, HTML, HOME),
            "https://shaggy.example/services": (200, HTML, SERVICES),
            "https://shaggy.example/contact": (500, {}, b""),
        },
    )
    monkeypatch.setattr(knowledge, "resolve", fake.resolve)
    monkeypatch.setattr(knowledge, "request", fake.request)
    return fake


# Choosing pages


def test_only_useful_pages_of_the_same_site_are_chosen() -> None:
    page = Page(
        url="https://shaggy.example/",
        text="",
        links=[
            ("https://shaggy.example/", "Home"),
            ("https://www.shaggy.example/services#top", "Services"),
            ("https://shaggy.example/services", "Our services"),
            ("https://shaggy.example/logo.png", "Prices"),
            ("https://other.example/prices", "Prices"),
            ("mailto:hi@shaggy.example", "Contact"),
            ("https://shaggy.example/blog/puppy-tips", "Blog"),
            ("https://shaggy.example/about-us", "About"),
            ("https://shaggy.example/team", "Meet us"),
            ("https://shaggy.example/hours", "When"),
            ("https://shaggy.example/faq", "FAQ"),
        ],
    )
    assert website_details.useful_links(page) == [
        "https://www.shaggy.example/services",
        "https://shaggy.example/services",
        "https://shaggy.example/about-us",
        "https://shaggy.example/team",
    ]


def test_the_home_page_and_its_useful_pages_are_read(site) -> None:
    pages, text = website_details.read_site("https://shaggy.example/")
    # The contact page failed and is skipped; the logo, blog and other sites aren't read.
    assert pages == ["https://shaggy.example/", "https://shaggy.example/services"]
    assert [path for _, _, path in site.requested] == ["/", "/services", "/contact"]
    assert "Page: https://shaggy.example/services\nServices" in text
    assert "Tuesday to Saturday 9am to 5pm" in text


def test_a_home_page_that_cannot_be_read_stops_it(site) -> None:
    site.pages["https://shaggy.example/"] = (404, {}, b"")
    with pytest.raises(KnowledgeError) as error:
        website_details.read_site("https://shaggy.example/")
    assert error.value.code == "page_unreachable"


# Checking what was found

TEXT = (
    "Call us at (408) 555-0134. Visit 12 Market St, San Jose, CA 95113.\n"
    "Full Groom: bath, haircut and nails. Small dogs $65, 90 minutes. Large dogs $105.\n"
    "Nail Trim $15\nTuesday to Saturday 9am to 5pm. Sunday 10–2.\n"
    "Bath & Tidy from $40\nYour groomers: Sam and Jo."
)


def hours(weekday, opens, closes, quote):
    return {"weekday": weekday, "opens_at": opens, "closes_at": closes, "quote": quote}


def test_only_what_the_site_says_is_kept() -> None:
    found, dropped = website_details.check(
        {
            "business_phone": {"value": "(408) 555-0134", "quote": "Call us at (408) 555-0134"},
            "address": {
                "value": "12 Market St, San Jose, CA 95113",
                "quote": "Visit 12 Market St, San Jose, CA 95113",
            },
            "hours": [
                hours(1, "09:00", "17:00", "Tuesday to Saturday 9am to 5pm"),
                hours(2, "09:00", "17:00", "Tuesday to Saturday 9am to 5pm"),
                hours(6, "10:00", "14:00", "Sunday 10–2"),
                # The quote says 9, not 8.
                hours(3, "08:00", "17:00", "Tuesday to Saturday 9am to 5pm"),
                # Twice the same day.
                hours(1, "10:00", "17:00", "Tuesday to Saturday 9am to 5pm"),
                # Closes before it opens.
                hours(4, "17:00", "09:00", "Tuesday to Saturday 9am to 5pm"),
                # Not on the site.
                hours(0, "09:00", "17:00", "Monday 9am to 5pm"),
            ],
            "services": [
                {
                    "name": "Full Groom",
                    "description": "Bath, haircut and nails.",
                    "quote": "Full Groom: bath, haircut and nails. Small dogs $65, 90 minutes. "
                    "Large dogs $105.",
                    "prices": [
                        {"size": "small", "price_dollars": 65, "duration_minutes": 90},
                        # A length the site doesn't give is left blank.
                        {"size": "large", "price_dollars": 105, "duration_minutes": 150},
                        # A price the site doesn't give is left blank.
                        {"size": "medium", "price_dollars": 85, "duration_minutes": None},
                        {"size": "any", "price_dollars": 65, "duration_minutes": 90},
                    ],
                },
                {
                    "name": "Bath and Tidy",
                    "description": "",
                    "quote": "Bath & Tidy from $40",
                    "prices": [{"size": "small", "price_dollars": 40, "duration_minutes": None}],
                },
                {
                    "name": "Teeth Cleaning",
                    "description": "",
                    "quote": "Nail Trim $15",
                    "prices": [],
                },
            ],
            "team": [
                {"name": "Sam", "quote": "Your groomers: Sam and Jo"},
                {"name": "Jo", "quote": "Your groomers: Sam and Jo"},
                {"name": "Alex", "quote": "Your groomers: Sam and Jo"},
            ],
        },
        TEXT,
        "pet_grooming",
    )
    assert found["business_phone"] == {
        "value": "(408) 555-0134",
        "quote": "Call us at (408) 555-0134",
    }
    assert found["address"]["value"] == "12 Market St, San Jose, CA 95113"
    assert [(h["weekday"], h["opens_at"], h["closes_at"]) for h in found["hours"]] == [
        (1, "09:00", "17:00"),
        (2, "09:00", "17:00"),
        (6, "10:00", "14:00"),
    ]
    assert found["services"] == [
        {
            "name": "Full Groom",
            "description": "Bath, haircut and nails.",
            "prices": [
                {"size": "small", "duration_minutes": 90, "price_cents": 6500},
                {"size": "large", "duration_minutes": None, "price_cents": 10500},
                {"size": "medium", "duration_minutes": None, "price_cents": None},
            ],
            "quote": "Full Groom: bath, haircut and nails. Small dogs $65, 90 minutes. "
            "Large dogs $105",
        },
        {
            "name": "Bath and Tidy",
            "description": "",
            "prices": [{"size": "small", "duration_minutes": None, "price_cents": 4000}],
            "quote": "Bath & Tidy from $40",
        },
    ]
    assert [p["name"] for p in found["team"]] == ["Sam", "Jo"]
    assert dropped == 6


def test_a_phone_or_address_the_site_does_not_give_is_dropped() -> None:
    found, dropped = website_details.check(
        {
            "business_phone": {"value": "(408) 555-9999", "quote": "Call us at (408) 555-0134"},
            "address": {"value": "99 Main St", "quote": "Visit 12 Market St, San Jose"},
            "hours": [],
            "services": [],
            "team": [],
        },
        TEXT,
        "pet_grooming",
    )
    assert found["business_phone"] is None and found["address"] is None
    assert dropped == 2


def test_salons_get_one_price_row() -> None:
    found, _ = website_details.check(
        {
            "business_phone": None,
            "address": None,
            "hours": [],
            "services": [
                {
                    "name": "Nail Trim",
                    "description": "",
                    "quote": "Nail Trim $15",
                    "prices": [
                        {"size": "small", "price_dollars": 15, "duration_minutes": None},
                        {"size": "any", "price_dollars": 15, "duration_minutes": None},
                    ],
                }
            ],
            "team": [],
        },
        TEXT,
        "hair_salon",
    )
    assert found["services"][0]["prices"] == [
        {"size": "any", "duration_minutes": None, "price_cents": 1500}
    ]


def test_the_request_asks_for_sizes_of_this_type_of_business() -> None:
    assert website_details.schema(["any"])["properties"]["services"]["items"]["properties"][
        "prices"
    ]["items"]["properties"]["size"]["enum"] == ["any"]


# The endpoint


def use_settings(**values) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(**values)


def auth(client) -> dict:
    token = client.post(
        f"{API}/signup",
        json={
            "full_name": "Sam Rivera",
            "email": "sam@example.com",
            "password": "correct horse battery",
            "accept_terms": True,
        },
        headers={"X-Forwarded-For": "203.0.113.5"},
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


REPLY = {
    "business_phone": {"value": "(408) 555-0134", "quote": "Call us at (408) 555-0134"},
    "address": None,
    "hours": [hours(1, "09:00", "17:00", "Tuesday to Saturday 9am to 5pm")],
    "services": [
        {
            "name": "Nail Trim",
            "description": "",
            "quote": "Nail Trim $15",
            "prices": [{"size": "small", "price_dollars": 15, "duration_minutes": None}],
        }
    ],
    "team": [{"name": "Sam", "quote": "Your groomers: Sam and Jo"}],
}


@pytest.fixture
def drafting(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    def post(settings, body):
        sent.append(body)
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(REPLY)}],
                }
            ],
        }

    monkeypatch.setattr(knowledge, "post", post)
    use_settings(openai_api_key="test-key", knowledge_runs_per_hour=2)
    return sent


def test_the_saved_website_fills_in_details(client, site, drafting, db: Session) -> None:
    headers = auth(client)
    client.patch(
        f"{API}/profile",
        headers=headers,
        json={
            "business_type": "pet_grooming",
            "details": {"website_url": "https://shaggy.example/"},
        },
    )
    response = client.post(f"{API}/profile/website/read", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["pages"] == ["https://shaggy.example/", "https://shaggy.example/services"]
    assert body["business_phone"]["value"] == "(408) 555-0134"
    assert body["address"] is None
    assert body["hours"] == [{**REPLY["hours"][0]}]
    assert body["services"][0]["prices"] == [
        {"size": "small", "duration_minutes": None, "price_cents": 1500}
    ]
    assert body["team"] == [{"name": "Sam", "quote": "Your groomers: Sam and Jo"}]
    assert body["dropped"] == 0
    # Asked for dog sizes, with the site's text as data.
    assert '"small"' in json.dumps(drafting[0]["text"]["format"]["schema"])
    assert "Page: https://shaggy.example/services" in drafting[0]["input"][0]["content"]
    # Nothing is saved to the profile.
    details = client.get(f"{API}/profile", headers=headers).json()["details"]
    assert details["hours"] == [] and details["services"] == []
    assert db.scalar(select(func.count()).select_from(KnowledgeRun)) == 1


def test_a_website_must_be_saved_first(client, drafting) -> None:
    response = client.post(f"{API}/profile/website/read", headers=auth(client))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_website"


def test_reading_the_website_is_off_without_an_openai_key(client) -> None:
    use_settings(openai_api_key="")
    response = client.post(f"{API}/profile/website/read", headers=auth(client))
    assert response.status_code == 503


def test_a_private_website_address_is_refused(client, drafting, monkeypatch) -> None:
    monkeypatch.setattr(knowledge, "resolve", lambda host, port: ["10.0.0.7"])
    headers = auth(client)
    client.patch(
        f"{API}/profile",
        headers=headers,
        json={"details": {"website_url": "http://intranet.example"}},
    )
    response = client.post(f"{API}/profile/website/read", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "page_not_allowed"
    assert drafting == []


def test_reading_the_website_shares_the_hourly_limit(client, site, drafting) -> None:
    headers = auth(client)
    client.patch(
        f"{API}/profile",
        headers=headers,
        json={"details": {"website_url": "https://shaggy.example/"}},
    )
    codes = [
        client.post(f"{API}/profile/website/read", headers=headers).status_code for _ in range(3)
    ]
    assert codes == [200, 200, 429]
