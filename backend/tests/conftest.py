import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://pawcare:pawcare@localhost:5440/pawcare_test"
)
# Set before the app loads settings so tests can never touch the dev database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["CORS_ALLOWED_ORIGINS"] = "http://localhost:5173"
os.environ["BUSINESS_SLUG"] = "pawcare-demo"

from app.db import get_db, get_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    BlockedPeriod,
    Booking,
    BookingDraft,
    CallbackRequest,
    StaffSession,
    StaffUser,
    SubscriberAccount,
    VoiceSession,
)
from app.seed import seed_business  # noqa: E402

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def ensure_database_exists() -> None:
    url = make_url(TEST_DATABASE_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    ensure_database_exists()
    config = Config(str(ALEMBIC_INI))
    # Starting from base exercises every downgrade as well as every upgrade.
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    get_engine().dispose()


@pytest.fixture
def db() -> Iterator[Session]:
    with Session(get_engine()) as session:
        session.execute(delete(Booking))
        session.execute(delete(BookingDraft))
        session.execute(delete(BlockedPeriod))
        session.execute(delete(StaffSession))
        session.execute(delete(CallbackRequest))
        session.execute(delete(VoiceSession))
        session.execute(delete(StaffUser))
        session.execute(delete(SubscriberAccount))
        seed_business(session)
        session.commit()
        yield session


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def database_down() -> Iterator[None]:
    """Route requests to a closed port, as if Postgres had gone away."""
    dead_engine = create_engine(
        "postgresql+psycopg://pawcare:secret-password@127.0.0.1:1/pawcare",
        connect_args={"connect_timeout": 1},
    )

    def dead_db() -> Iterator[Session]:
        with Session(dead_engine) as session:
            yield session

    app.dependency_overrides[get_db] = dead_db
    yield
    app.dependency_overrides.clear()
