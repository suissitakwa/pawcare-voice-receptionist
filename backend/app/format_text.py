from datetime import time

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def weekday_name(weekday: int) -> str:
    return WEEKDAYS[weekday]


def format_time(value: time) -> str:
    """Spoken-style time, such as 9 AM or 5:30 PM."""
    suffix = "AM" if value.hour < 12 else "PM"
    hour = value.hour % 12 or 12
    return f"{hour} {suffix}" if value.minute == 0 else f"{hour}:{value.minute:02d} {suffix}"
