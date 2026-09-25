from datetime import date, datetime, timezone

from taps.config import Config, Place, Settings
from taps.corrections import Corrections, ManualEntry
from taps.model import Serving
from taps.sources.manual import MANUAL_KEEP_DAYS, manual_result

NOW = datetime(2026, 9, 23, 21, 30, tzinfo=timezone.utc)  # 2026-09-24 01:30 in Yerevan


def place(pid, kind="bar"):
    return Place(id=pid, name=pid.title(), kind=kind, sources={"untappd_checkins": {"slug": pid, "venue_id": 1}})


CONFIG = Config(
    places={p.id: p for p in (place("tap-station"), place("dors", "brewpub"))},
    breweries=(),
    settings=Settings(),
)


def entry(place="tap-station", day=date(2026, 9, 24), by="Аня", brewery="379", beer="Hazy Pale", untappd=None,
          **details):
    return ManualEntry(
        id=f"{place}|{day.isoformat()}|{by}", place=place, brewery=brewery, beer=beer,
        untappd_id=untappd, by=by, date=day, **details,
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
    result = run(*(entry(day=d, beer=f"Hazy Pale {d.day}") for d in days))   # one beer per day: not servings
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


def test_entry_details_reach_the_sighting():
    e = ManualEntry(id="tap-station|2026-09-24|Аня", place="tap-station", brewery="379", beer="Weizen",
                    untappd_id=None, by="Аня", date=date(2026, 9, 24),
                    container="draft", style="Wheat Beer", abv=5.0, ibu=11, price_amd=1500)
    [s] = run(e).sightings
    assert (s.container, s.style, s.abv, s.ibu, s.price_amd) == ("draft", "Wheat Beer", 5.0, 11, 1500)


# --- several entries for one beer at one place are its servings -----------------------------------

def rodenbach(by="Instagram бара", **details):
    return entry(place="dors", by=by, brewery="Rodenbach", beer="Fruitage", untappd=1715344, **details)


def test_entries_of_one_beer_at_one_place_are_servings_of_one_sighting():
    [s] = run(rodenbach(container="draft", price_amd=2800), rodenbach(container="bottle")).sightings
    assert s.beer_key == "u:1715344"
    assert (s.container, s.price_amd, s.volume_ml) == ("draft", 2800, None)   # the first entry: single-serving readers
    assert s.servings == (Serving("draft", 2800), Serving("bottle"))


def test_a_later_entry_fills_in_what_the_first_one_lacks():
    named = rodenbach(container="draft", price_amd=2800)
    detailed = entry(place="dors", by="Instagram бара", brewery=None, beer=None, untappd=1715344,
                     container="bottle", style="Cherry Ale", abv=3.4, ibu=7)
    [s] = run(named, detailed).sightings
    assert (s.style, s.abv, s.ibu) == ("Cherry Ale", 3.4, 7)
    assert (s.brewery, s.name, s.title) == ("Rodenbach", "Fruitage", "Rodenbach Fruitage")
    [s] = run(detailed, named).sightings   # the names come from the entry that has them, whichever is first
    assert (s.brewery, s.name, s.title) == ("Rodenbach", "Fruitage", "Rodenbach Fruitage")


def test_identical_entries_are_one_serving_and_the_first_one_keeps_the_announcement():
    first = rodenbach(container="draft", price_amd=2800)
    again = rodenbach(container="draft", price_amd=2800, day=date(2026, 9, 23), by="Олег")   # another day, another friend
    [s] = run(first, again).sightings
    assert (s.container, s.price_amd, s.servings) == ("draft", 2800, ())
    assert (s.manual_id, s.manual_by, s.manual_date) == ("dors|2026-09-24|Instagram бара", "Instagram бара", "2026-09-24")


def test_a_draft_entry_with_another_price_is_another_serving():
    [s] = run(rodenbach(container="draft", price_amd=1400), rodenbach(container="draft", price_amd=2000)).sightings
    assert s.servings == (Serving("draft", 1400), Serving("draft", 2000))


def test_other_places_and_other_beers_stay_separate_sightings_in_order_of_appearance():
    result = run(rodenbach(container="draft"), entry(untappd=1715344), rodenbach(container="bottle"),
                 entry(beer="Gose"))
    assert [(s.place_id, s.beer_key, len(s.servings)) for s in result.sightings] == [
        ("dors", "u:1715344", 2), ("tap-station", "u:1715344", 0), ("tap-station", "n:379 gose", 0)]


def test_an_entry_outside_the_window_gives_no_serving():
    result = run(rodenbach(container="draft", price_amd=2800), rodenbach(container="bottle", day=date(2026, 9, 1)))
    [s] = result.sightings
    assert (s.container, s.servings) == ("draft", ())


def test_entries_that_an_alias_joins_are_servings_of_one_sighting():
    by_id = rodenbach(container="draft", price_amd=2800)
    by_name = entry(place="dors", by="Instagram бара", brewery="Rodenbach", beer="Fruitage", container="bottle")
    aliases = {"n:rodenbach fruitage": "u:1715344"}   # the name-only entry is the same beer as untappd: 1715344
    [s] = run(by_id, by_name, aliases=aliases).sightings
    assert (s.beer_key, s.servings) == ("u:1715344", (Serving("draft", 2800), Serving("bottle")))
    [s] = run(by_name, by_id, aliases=aliases).sightings   # whichever comes first, the Untappd id and link are kept
    assert (s.beer_key, s.untappd_beer_id, s.url) == ("u:1715344", 1715344, "https://untappd.com/beer/1715344")
    assert s.servings == (Serving("bottle"), Serving("draft", 2800))
