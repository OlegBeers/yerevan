from datetime import datetime, timedelta, timezone

from taps.checkin_log import CheckinLogEntry
from taps.config import Config, Place, Settings
from taps.stats_data import build_stats_data
from taps.state import BeerRec, PairRec, VenueRec, empty_state

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def entry(id, days_ago=0, venue_id=1, username="ann", beer_id=100, brewery="Dargett", rating=None):
    return CheckinLogEntry(id=id, time=(NOW - timedelta(days=days_ago)).isoformat(timespec="seconds"),
                          venue_id=venue_id, username=username, beer_id=beer_id, brewery=brewery, rating=rating)


CONFIG = Config(places={"dors": Place(id="dors", name="Dors Bar", kind="bar",
                                      sources={"untappd_checkins": {"slug": "dors", "venue_id": 1}})},
                breweries=(), settings=Settings())


def all_period(state, config, log, now=NOW):
    return build_stats_data(state, config, log, now)["periods"]["all"]


# --- top-level shape and periods ------------------------------------------------------------------

def test_build_stats_data_has_generated_at_first_record_at_and_the_three_periods():
    log = [entry(1, days_ago=200), entry(2, days_ago=1)]
    data = build_stats_data(empty_state(NOW), CONFIG, log, NOW)
    assert data["generated_at"] == NOW.isoformat(timespec="seconds")
    assert data["first_record_at"] == (NOW - timedelta(days=200)).isoformat(timespec="seconds")
    assert set(data["periods"]) == {"30d", "90d", "all"}


def test_first_record_at_is_none_for_an_empty_log():
    assert build_stats_data(empty_state(NOW), CONFIG, [], NOW)["first_record_at"] is None


def test_periods_filter_by_age_but_all_time_keeps_everything():
    log = [entry(1, days_ago=10), entry(2, days_ago=50), entry(3, days_ago=100), entry(4, days_ago=200)]
    data = build_stats_data(empty_state(NOW), CONFIG, log, NOW)
    assert data["periods"]["30d"]["summary"]["checkins"] == 1
    assert data["periods"]["90d"]["summary"]["checkins"] == 2
    assert data["periods"]["all"]["summary"]["checkins"] == 4


# --- summary -----------------------------------------------------------------------------------

def test_summary_counts_checkins_people_venues_beers_breweries_and_avg_rating():
    log = [
        entry(1, username="ann", beer_id=100, brewery="Dargett", venue_id=1, rating=4.0),
        entry(2, username="bob", beer_id=200, brewery="Gyumri", venue_id=2, rating=3.0),
        entry(3, username="ann", beer_id=100, brewery="Dargett", venue_id=1, rating=None),
    ]
    summary = all_period(empty_state(NOW), CONFIG, log)["summary"]
    assert summary == {"checkins": 3, "people": 2, "venues": 2, "beers": 2, "breweries": 2, "avg_rating": 3.5}


def test_summary_avg_rating_is_none_when_no_checkin_carries_a_rating():
    log = [entry(1, rating=None), entry(2, rating=None)]
    assert all_period(empty_state(NOW), CONFIG, log)["summary"]["avg_rating"] is None


# --- venues ranking ------------------------------------------------------------------------------

def test_venues_ranking_orders_by_checkins_and_reports_visitors_rating_and_top_beer():
    log = [
        entry(1, venue_id=1, username="ann", beer_id=100),
        entry(2, venue_id=1, username="bob", beer_id=100),
        entry(3, venue_id=1, username="bob", beer_id=100, rating=4.5),
        entry(4, venue_id=2, username="ann", beer_id=200, rating=3.0),
    ]
    venues = all_period(empty_state(NOW), CONFIG, log)["venues"]
    assert [v["venue_id"] for v in venues] == [1, 2]
    dors = venues[0]
    assert (dors["name"], dors["checkins"], dors["unique_visitors"], dors["avg_rating"]) == ("Dors Bar", 3, 2, 4.5)
    assert dors["top_beer"]["beer_id"] == 100


def test_venue_name_falls_back_to_state_venues_for_an_untracked_venue():
    state = empty_state(NOW)
    state.venues["99"] = VenueRec(name="Discovered Bar", url="https://untappd.com/v/x/99", country="Armenia")
    venues = all_period(state, CONFIG, [entry(1, venue_id=99)])["venues"]
    assert venues[0]["name"] == "Discovered Bar"


# --- top beers / breweries / styles ---------------------------------------------------------------

def test_top_beers_and_breweries_ranked_by_checkins():
    log = [entry(1, beer_id=100, brewery="Dargett"), entry(2, beer_id=100, brewery="Dargett"),
          entry(3, beer_id=200, brewery="Gyumri")]
    period = all_period(empty_state(NOW), CONFIG, log)
    assert period["top_beers"] == [{"beer_id": 100, "name": None, "brewery": "Dargett", "checkins": 2},
                                   {"beer_id": 200, "name": None, "brewery": "Gyumri", "checkins": 1}]
    assert period["top_breweries"] == [{"brewery": "Dargett", "checkins": 2}, {"brewery": "Gyumri", "checkins": 1}]


def test_top_beers_uses_the_beer_name_known_from_state_beers():
    state = empty_state(NOW)
    state.beers["u:100"] = BeerRec(first_seen_city=NOW.isoformat(timespec="seconds"), name="Cherry Ale", style="Fruit Beer")
    top = all_period(state, CONFIG, [entry(1, beer_id=100)])["top_beers"]
    assert top[0]["name"] == "Cherry Ale"


def test_top_beers_falls_back_to_the_beer_name_known_from_a_pair():
    state = empty_state(NOW)
    state.pairs["dors"] = {"u:100": PairRec(first_seen=NOW.isoformat(timespec="seconds"),
                                            last_seen=NOW.isoformat(timespec="seconds"),
                                            info={"name": "Cherry Ale", "style": "Fruit Beer"})}
    top = all_period(state, CONFIG, [entry(1, beer_id=100)])["top_beers"]
    assert top[0]["name"] == "Cherry Ale"


def test_top_styles_uses_the_shared_style_family_helper_with_an_unknown_bucket():
    state = empty_state(NOW)
    state.beers["u:100"] = BeerRec(first_seen_city=NOW.isoformat(timespec="seconds"), style="IPA - Imperial / Double")
    state.beers["u:200"] = BeerRec(first_seen_city=NOW.isoformat(timespec="seconds"), style="Stout - American")
    log = [entry(1, beer_id=100), entry(2, beer_id=200), entry(3, beer_id=300)]   # 300: no known style
    styles = all_period(state, CONFIG, log)["top_styles"]
    assert {"style": "DIPA", "checkins": 1} in styles
    assert {"style": "Stout", "checkins": 1} in styles
    assert {"style": None, "checkins": 1} in styles


# --- brewery origin (Armenian vs imported) --------------------------------------------------------

def test_brewery_origin_buckets_by_the_known_beer_country_with_an_unknown_bucket():
    state = empty_state(NOW)
    state.beers["u:100"] = BeerRec(first_seen_city=NOW.isoformat(timespec="seconds"), country="Armenia")
    state.beers["u:200"] = BeerRec(first_seen_city=NOW.isoformat(timespec="seconds"), country="Belgium")
    log = [entry(1, beer_id=100, brewery="Dargett"), entry(2, beer_id=200, brewery="Rodenbach"),
          entry(3, beer_id=300, brewery="Mystery")]   # 300: no known country
    origin = all_period(state, CONFIG, log)["brewery_origin"]
    assert origin == {"armenian": 1, "imported": 1, "unknown": 1}


# --- new breweries/beers first seen in the period -------------------------------------------------

def test_new_beers_and_breweries_are_those_first_seen_within_the_period():
    log = [entry(1, days_ago=200, beer_id=100, brewery="Dargett"),    # first seen long ago
          entry(2, days_ago=10, beer_id=200, brewery="Gyumri")]      # first seen recently: new in 30d
    data = build_stats_data(empty_state(NOW), CONFIG, log, NOW)
    new_30d = data["periods"]["30d"]["new_beers"]
    assert [b["beer_id"] for b in new_30d] == [200]
    assert data["periods"]["30d"]["new_breweries"] == [{"brewery": "Gyumri"}]
    assert [b["beer_id"] for b in data["periods"]["all"]["new_beers"]] == [100, 200]   # everything is "new" all-time


def test_a_beer_re_seen_in_the_period_is_not_new_if_first_seen_earlier():
    log = [entry(1, days_ago=200, beer_id=100), entry(2, days_ago=1, beer_id=100)]
    new_30d = build_stats_data(empty_state(NOW), CONFIG, log, NOW)["periods"]["30d"]["new_beers"]
    assert new_30d == []


# --- people leaderboard --------------------------------------------------------------------------

def test_leaderboard_requires_at_least_three_checkins_and_reports_unique_beers_and_venues():
    log = [
        entry(1, username="ann", beer_id=100, venue_id=1), entry(2, username="ann", beer_id=100, venue_id=1),
        entry(3, username="ann", beer_id=200, venue_id=2),
        entry(4, username="bob", beer_id=100, venue_id=1), entry(5, username="bob", beer_id=200, venue_id=1),
    ]
    people = all_period(empty_state(NOW), CONFIG, log)["people"]
    assert [p["username"] for p in people] == ["ann"]   # bob has only 2 check-ins: below the minimum
    ann = people[0]
    assert (ann["rank"], ann["checkins"], ann["unique_beers"], ann["venues"]) == (1, 3, 2, 2)
    assert ann["profile_url"] == "https://untappd.com/user/ann"


def test_leaderboard_ranks_by_checkins_then_username_and_caps_at_100():
    log = []
    cid = 1
    for i in range(101):
        username = f"user{i:03d}"
        for _ in range(3):
            log.append(entry(cid, username=username, beer_id=cid))
            cid += 1
    people = all_period(empty_state(NOW), CONFIG, log)["people"]
    assert len(people) == 100
    assert [p["rank"] for p in people] == list(range(1, 101))
