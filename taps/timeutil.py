"""Time helpers: everything is aware UTC internally, Asia/Yerevan for "today"."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

YEREVAN = ZoneInfo("Asia/Yerevan")


def _aware(dt: datetime) -> datetime:
    if dt.utcoffset() is None:
        raise ValueError(f"naive datetime: {dt!r}")
    return dt


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return _aware(dt).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return _aware(datetime.fromisoformat(s)).astimezone(timezone.utc)


def to_yerevan(dt: datetime) -> datetime:
    return _aware(dt).astimezone(YEREVAN)


def yerevan_date(dt: datetime) -> str:
    return to_yerevan(dt).date().isoformat()


def age_days(then: datetime, now: datetime) -> float:
    return (now - then).total_seconds() / 86400
