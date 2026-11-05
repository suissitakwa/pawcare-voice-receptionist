from datetime import UTC, datetime


def get_now() -> datetime:
    return datetime.now(UTC)
