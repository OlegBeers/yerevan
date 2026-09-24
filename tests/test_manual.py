from datetime import date, datetime, timezone

from taps.config import Config, Place, Settings
from taps.corrections import Corrections, ManualEntry
from taps.sources.manual import MANUAL_KEEP_DAYS, manual_result

NOW = datetime(2026, 9, 23, 21, 30, tzinfo=timezone.utc)  # 2026-09-24 01:30 in Yerevan


def place(pid, kind="bar"):
    return Place(id=pid, name=pid.title(), kind=kind, sources={"untappd_checkins": {"slug": pid, "venue_id": 1}})


CONFIG = Config(
    places={p.id: p for p in (place("tap-station"), place("dors", "brewpub"))},
    breweries=(),
    settings=Settings(),
)


def entry(place="tap-station", day=date(2026, 9, 24), by="Аня", brewery="379", beer="Hazy Pale", untappd=None):
    return ManualEntry(
        id=f"{place}|{day.isoformat()}|{by}", place=place, brewery=brewery, beer=beer,
        untappd_id=untappd, by=by, date=day,
    )


def run(*entries, **kwargs):
    return manual_result(Corrections(sightings=entries, **kwargs), CONFIG, NOW)


def test_no_entries_is_ok():
    result = run()
    assert (result.key, result.source, result.ok, result.error) == ("manual", "manual", True, None)
    assert result.sightings == []
    assert result.place_id is None
    assert result.full is True


def test_named_entry():
    [s] = run(entry()).sightings
    assert s.place_id == "tap-station"
    assert (s.source, s.kind) == ("manual", "manual")
    assert s.beer_key == "n:379 hazy pale"
    assert (s.title, s.name, s.brewery) == ("379 Hazy Pale", "Hazy Pale", "379")
    assert s.seen_at == NOW
    assert (s.manual_id, s.manual_by, s.manual_date) == ("tap-station|2026-09-24|Аня", "Аня", "2026-09-24")
    assert (s.untappd_beer_id, s.url) == (None, None)


def test_entry_without_brewery():
    [s] = run(entry(brewery=None, beer="Hazy Pale")).sightings
    assert s.beer_key == "n:hazy pale"
    assert s.title == "Hazy Pale"


def test_untappd_entry_uses_u_key():
    [by_id, named] = run(
        entry(place="dors", by="Олег", brewery=None, beer=None, untappd=1234567),
        entry(untappd=7654321),
    ).sightings
    assert by_id.beer_key == "u:1234567"
    assert by_id.untappd_beer_id == 1234567
    assert by_id.url == "https://untappd.com/beer/1234567"
    assert by_id.name == "Untappd #1234567"
    assert by_id.manual_id == "dors|2026-09-24|Олег"
    assert (named.beer_key, named.name) == ("u:7654321", "Hazy Pale")


def test_brewery_aliases_apply_to_n_key():
    [s] = run(entry(brewery="V.Engelman", beer="IPA"), brewery_aliases={"v engelman": "volfas engelman"}).sightings
    assert s.beer_key == "n:volfas engelman ipa"
    assert s.brewery == "V.Engelman"


def test_only_last_14_yerevan_days():
    assert MANUAL_KEEP_DAYS == 14
    days = [date(2026, 9, 24), date(2026, 9, 10), date(2026, 9, 9), date(2026, 9, 25)]
    result = run(*(entry(day=d) for d in days))
    # today in Yerevan is 2026-09-24 (UTC is still 09-23); 09-10 is 14 days ago; 09-09 is too old; 09-25 is future
    assert [s.manual_date for s in result.sightings] == ["2026-09-24", "2026-09-10"]
    assert result.ok is True


def test_all_entries_outside_window_is_still_ok():
    result = run(entry(day=date(2026, 8, 1)))
    assert result.ok is True
    assert result.sightings == []


def test_place_not_in_config_is_skipped():
    result = run(entry(place="tuf"), entry(place="dors"))
    assert [s.place_id for s in result.sightings] == ["dors"]
