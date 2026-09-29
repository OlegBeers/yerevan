from datetime import datetime, timedelta, timezone

from taps.checkin_log import CheckinLogEntry, load_checkin_log, normalize_username, record_checkins, save_checkin_log
from taps.sources.untappd_checkins import Checkin

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
ARMENIA = {12281551, 11856429}


def checkin(id, venue_id, username="user1", beer_id=100, brewery="Dargett", rating=None, at_home=False,
           created_at=NOW):
    return Checkin(checkin_id=id, beer_id=beer_id, beer_name="Beer", brewery=brewery, venue_id=venue_id,
                   venue_name="Venue", serving="Draft", at_home=at_home, created_at=created_at,
                   username=username, rating=rating)


def test_record_checkins_keeps_only_armenia_venues_with_a_venue_and_not_at_home():
    checkins = [
        checkin(1, 12281551),                       # a known Armenia venue: kept
        checkin(2, 99999999),                        # not an Armenia venue: dropped
        checkin(3, None),                             # no venue: dropped
        checkin(4, 12281551, at_home=True),           # at-home: dropped
    ]
    log = record_checkins([], checkins, ARMENIA, set(), NOW)
    assert [e.id for e in log] == [1]


def test_record_checkins_stores_the_expected_fields():
    [entry] = record_checkins([], [checkin(1, 12281551, username="anna", beer_id=555, brewery="Dargett",
                                           rating=4.25)], ARMENIA, set(), NOW)
    assert entry == CheckinLogEntry(id=1, time=NOW.isoformat(timespec="seconds"), venue_id=12281551,
                                    username="anna", beer_id=555, brewery="Dargett", rating=4.25)


def test_record_checkins_drops_hidden_users_and_purges_existing_entries():
    existing = [CheckinLogEntry(id=9, time=NOW.isoformat(timespec="seconds"), venue_id=12281551,
                                username="hidden_one", beer_id=1, brewery="X", rating=None)]
    checkins = [checkin(1, 12281551, username="hidden_one"), checkin(2, 12281551, username="visible")]
    log = record_checkins(existing, checkins, ARMENIA, {"hidden_one"}, NOW)
    assert [e.id for e in log] == [2]


def test_normalize_username_casefolds_strips_at_and_profile_url():
    assert normalize_username("SomeUser") == "someuser"
    assert normalize_username("@SomeUser") == "someuser"
    assert normalize_username("https://untappd.com/user/SomeUser") == "someuser"
    assert normalize_username("https://untappd.com/user/SomeUser/") == "someuser"


def test_record_checkins_hidden_users_match_regardless_of_case_at_or_url_form():
    checkins = [checkin(1, 12281551, username="SomeUser"), checkin(2, 12281551, username="visible")]
    log = record_checkins([], checkins, ARMENIA, {"@someuser"}, NOW)
    assert [e.id for e in log] == [2]


def test_record_checkins_purges_an_existing_entry_regardless_of_stored_case():
    existing = [CheckinLogEntry(id=9, time=NOW.isoformat(timespec="seconds"), venue_id=12281551,
                                username="SomeUser", beer_id=1, brewery="X", rating=None)]
    log = record_checkins(existing, [], ARMENIA, {"someuser"}, NOW)
    assert log == []


def test_record_checkins_dedupes_by_id_across_sources():
    # the same check-in id seen on both a venue page and a brewery page
    log = record_checkins([], [checkin(1, 12281551), checkin(1, 12281551)], ARMENIA, set(), NOW)
    assert len(log) == 1


def test_record_checkins_prunes_entries_older_than_120_days_and_sorts_by_id():
    fresh = CheckinLogEntry(id=5, time=NOW.isoformat(timespec="seconds"), venue_id=12281551,
                            username="u", beer_id=1, brewery="X", rating=None)
    stale = CheckinLogEntry(id=2, time=(NOW - timedelta(days=121)).isoformat(timespec="seconds"),
                            venue_id=12281551, username="u", beer_id=1, brewery="X", rating=None)
    log = record_checkins([fresh, stale], [checkin(3, 12281551)], ARMENIA, set(), NOW)
    assert [e.id for e in log] == [3, 5]


def test_save_and_load_checkin_log_roundtrip(tmp_path):
    path = tmp_path / "checkins.json"
    entries = [
        CheckinLogEntry(id=2, time=NOW.isoformat(timespec="seconds"), venue_id=1, username="a", beer_id=1,
                       brewery="X", rating=4.0),
        CheckinLogEntry(id=1, time=NOW.isoformat(timespec="seconds"), venue_id=1, username="b", beer_id=2,
                       brewery="Y", rating=None),
    ]
    save_checkin_log(path, entries)
    loaded = load_checkin_log(path)
    assert loaded == sorted(entries, key=lambda e: e.id)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2   # one compact record per line


def test_load_checkin_log_missing_file_is_empty(tmp_path):
    assert load_checkin_log(tmp_path / "missing.json") == []
