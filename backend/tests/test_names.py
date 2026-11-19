"""Names spelled letter by letter on a call are joined before they're saved."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Business
from app.names import join_spelled
from app.voice import build_instructions, message_instructions
from tests.test_booking_api import NOW, details, fixed_clock, prepare  # noqa: F401 - fixture


@pytest.mark.parametrize(
    ("heard", "saved"),
    [
        ("T A K W A", "Takwa"),
        ("T-A-K-W-A", "Takwa"),
        ("t. a. k. w. a.", "Takwa"),
        ("T,A,K,W,A", "Takwa"),
        ("Takwa S U I S S I", "Takwa Suissi"),
        ("T A K W A  S U I S S I", "Takwa Suissi"),
        ("T-A-K-W-A S-U-I-S-S-I", "Takwa Suissi"),
        ("B E L L A", "Bella"),
        # Ordinary names and initials stay as they are.
        ("Takwa", "Takwa"),
        ("Mary Ann", "Mary Ann"),
        ("A J Smith", "A J Smith"),
        ("Ann-Marie", "Ann-Marie"),
        ("O'Brien", "O'Brien"),
        ("Jo", "Jo"),
    ],
)
def test_spelled_names_are_joined(heard: str, saved: str) -> None:
    assert join_spelled(heard) == saved


def test_a_booking_saves_spelled_names_joined(client: TestClient) -> None:
    summary = prepare(client, owner_name="T A K W A", pet_name="M-I-L-A").json()["summary"]
    assert (summary["owner_name"], summary["pet_name"]) == ("Takwa", "Mila")


def test_the_message_taker_knows_the_services_and_reads_back(db: Session) -> None:
    demo = db.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    text = message_instructions(db, demo, None)
    assert "The services are: Bath and Tidy, Full Groom, Nail and Paw Care." in text
    assert "a word that sounds like a service name means that service" in text
    assert "what they need in one short sentence" in text
    assert "Never pass a spelling you guessed from how the name sounded" in text
    assert "Only the letters they spell count" in text


def test_the_receptionist_reads_back_a_callback_note(db: Session) -> None:
    demo = db.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    text = build_instructions(db, demo, NOW.date(), channel="phone")
    assert "including what they need in one short sentence using the service names" in text


def test_tools_ask_for_the_spelled_letters_not_a_guess() -> None:
    from app.voice import TOOLS

    callback = next(t for t in TOOLS if t["name"] == "create_callback_request")
    description = callback["parameters"]["properties"]["name"]["description"]
    assert "Exactly the letters the caller spelled" in description
    assert "Never your guess" in description
