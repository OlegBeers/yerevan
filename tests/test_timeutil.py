from datetime import datetime, timedelta, timezone

import pytest

from taps.timeutil import YEREVAN, age_days, iso, parse_iso, to_yerevan, utcnow, yerevan_date


def test_utcnow_is_aware_utc():
    assert utcnow().utcoffset() == timedelta(0)


def test_iso_formats_utc_to_seconds():
    dt = datetime(2026, 9, 23, 14, 17, 5, 123456, tzinfo=timezone.utc)
    assert iso(dt) == "2026-09-23T14:17:05+00:00"


def test_iso_converts_other_zones_to_utc():
    assert iso(datetime(2026, 9, 23, 18, 17, tzinfo=YEREVAN)) == "2026-09-23T14:17:00+00:00"


def test_iso_parse_iso_round_trip():
    dt = datetime(2026, 9, 23, 14, 17, tzinfo=timezone.utc)
    back = parse_iso(iso(dt))
    assert back == dt
    assert back.tzinfo == timezone.utc


def test_parse_iso_normalizes_offset_and_z_to_utc():
    assert parse_iso("2026-09-23T18:17:00+04:00") == datetime(2026, 9, 23, 14, 17, tzinfo=timezone.utc)
    assert parse_iso("2026-09-23T18:17:00+04:00").tzinfo == timezone.utc
    assert parse_iso("2026-09-23T20:30:00Z") == datetime(2026, 9, 23, 20, 30, tzinfo=timezone.utc)


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        parse_iso("2026-09-23T14:17:00")
    with pytest.raises(ValueError):
        iso(datetime(2026, 9, 23, 14, 17))
    with pytest.raises(ValueError):
        yerevan_date(datetime(2026, 9, 23, 14, 17))


def test_to_yerevan_is_utc_plus_4():
    local = to_yerevan(parse_iso("2026-09-23T20:30:00Z"))
    assert local.utcoffset() == timedelta(hours=4)
    assert (local.day, local.hour, local.minute) == (24, 0, 30)


def test_yerevan_date_across_midnight():
    assert yerevan_date(parse_iso("2026-09-23T20:30:00Z")) == "2026-09-24"
    assert yerevan_date(parse_iso("2026-09-23T19:59:59Z")) == "2026-09-23"


def test_age_days():
    then = parse_iso("2026-09-20T12:00:00+00:00")
    now = parse_iso("2026-09-23T00:00:00+00:00")
    assert age_days(then, now) == 2.5
    assert age_days(now, then) == -2.5
