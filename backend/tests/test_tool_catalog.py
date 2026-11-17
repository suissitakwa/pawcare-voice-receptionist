"""The assistant's tools offer each business its own services and sizes."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import voice
from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Business, Service
from app.vapi import vapi_tools
from app.voice import TOOLS, ToolCatalog, browser_tools, phone_tools, tool_catalog

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 5, 10, 0, tzinfo=LA)


def business_id(db: Session, slug: str):
    return db.scalar(select(Business.id).where(Business.slug == slug))


def enums(tools: list[dict], name: str) -> dict[str, list[str] | None]:
    tool = next(tool for tool in tools if tool["name"] == name)
    properties = tool["parameters"]["properties"]
    return {field: properties[field].get("enum") for field in ("service", "size")}


def test_the_demo_catalog_gives_the_same_tools_as_before(db) -> None:
    assert browser_tools(tool_catalog(db, business_id(db, "pawcare-demo"))) == TOOLS


def test_another_business_gets_only_its_own_services(db, second_business) -> None:
    catalog = tool_catalog(db, business_id(db, second_business))
    assert catalog.services == ("bath-and-tidy",)
    for tools in (browser_tools(catalog), phone_tools(catalog)):
        assert enums(tools, "check_availability") == {
            "service": ["bath-and-tidy"],
            "size": ["small", "medium", "large", "extra-large"],
        }
        # Extra-large needs a staff assessment, so it can't be booked directly.
        assert enums(tools, "prepare_booking")["size"] == ["small", "medium", "large"]


def test_inactive_services_are_not_offered(db) -> None:
    db.execute(update(Service).where(Service.slug == "full-groom").values(is_active=False))
    db.commit()
    catalog = tool_catalog(db, business_id(db, "pawcare-demo"))
    assert "full-groom" not in catalog.services


def test_an_empty_catalog_leaves_the_value_open_rather_than_invalid() -> None:
    tools = browser_tools(ToolCatalog(services=(), sizes=("small",), bookable_sizes=()))
    assert enums(tools, "check_availability") == {"service": None, "size": ["small"]}
    assert enums(tools, "prepare_booking") == {"service": None, "size": None}


def test_the_shared_template_is_never_changed(db, second_business) -> None:
    before = [tool["parameters"]["properties"].get("service") for tool in TOOLS[:2]]
    browser_tools(tool_catalog(db, business_id(db, second_business)))
    assert [tool["parameters"]["properties"].get("service") for tool in TOOLS[:2]] == before


def test_vapi_tools_carry_the_catalog(db, second_business) -> None:
    tools = vapi_tools(tool_catalog(db, business_id(db, second_business)))
    check = next(t["function"] for t in tools if t["function"]["name"] == "check_availability")
    assert check["parameters"]["properties"]["service"]["enum"] == ["bath-and-tidy"]


# End to end: a browser session at the second business sends its catalog to OpenAI.


@pytest.fixture
def minted(monkeypatch) -> Iterator[list[dict]]:
    calls: list[dict] = []

    def fake_mint(settings, instructions, safety_id, tools=None):
        calls.append({"instructions": instructions, "tools": tools})
        return "ek_test_secret", int((NOW + timedelta(seconds=60)).timestamp())

    monkeypatch.setattr(voice, "mint_client_secret", fake_mint)
    settings = Settings(voice_mode="live", openai_api_key="fake-openai-key-for-tests")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_now] = lambda: NOW
    yield calls
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_now, None)


def test_browser_session_sends_the_business_catalog(
    client: TestClient, minted, second_business
) -> None:
    response = client.post(f"/api/v1/b/{second_business}/voice/sessions")
    assert response.status_code == 201
    [call] = minted
    assert enums(call["tools"], "check_availability")["service"] == ["bath-and-tidy"]
    assert "Second Studio" in call["instructions"]
