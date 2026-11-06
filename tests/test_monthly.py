from datetime import datetime, timezone

import pytest

from taps.checkin_log import CheckinLogEntry
from taps.config import Config, Place, Settings
from taps.monthly import build_monthly, month_pending, monthly_due, previous_month
from taps.state import BeerRec, PairRec, State, VenueRec
from taps.timeutil import YEREVAN, iso


def yv(month, day, hour=12, year=2026):
    """Yerevan wall-clock time as aware UTC."""
    return datetime(year, month, day, hour, 0, tzinfo=YEREVAN).astimezone(timezone.utc)


def place(pid, name, venue_id, short=None):
    return Place(id=pid, name=name, kind="bar", sources={"untappd_menu": {"slug": pid, "venue_id": venue_id}},
                 short_name=short)


CONFIG = Config(
    places={"gargoyle": place("gargoyle", "Gargoyle Bar", 1, short="Gargoyle"),
            "beatles": place("beatles", "Beatles Pub", 2),
            "parma": Place(id="parma", name="Parma", kind="shop", sources={"parma": {}})},
    breweries=(), settings=Settings(),
)
OTHER_VENUE = 99


def beer(beer_id, name):
    return f"u:{beer_id}", BeerRec(first_seen_city="Yerevan", name=name)


def state(**kw) -> State:
    s = State(started_at="2026-01-01T00:00:00+00:00", **kw)
    s.beers.update(dict([beer(10, "Alpha"), beer(11, "Beta"), beer(12, "Gamma"), beer(13, "Delta"),
                         beer(14, "Nameless Fan (Lager, Pale, 5%)")]))
    s.venues[str(OTHER_VENUE)] = VenueRec(name="Some Pub", url="https://untappd.com/v/x/99")
    return s


_id = iter(range(1, 10_000))


def ck(when, venue=1, user="u1", beer_id=10, rating=4.0, brewery="Zagovor Brewery"):
    return CheckinLogEntry(id=next(_id), time=iso(when), venue_id=venue, username=user, beer_id=beer_id,
                           brewery=brewery, rating=rating)


def pair(event_at, notified_at=None):
    return PairRec(first_seen="x", last_seen="x", event_at=iso(event_at) if event_at else None,
                   notified_at=notified_at)


# --- due logic ------------------------------------------------------------------------------

def test_previous_month_uses_the_yerevan_date():
    assert previous_month(yv(11, 1, 10)) == "2026-10"
    assert previous_month(yv(1, 1, 10)) == "2025-12"
    # 00:30 on 1 Nov in Yerevan is still 31 Oct 20:30 UTC: the Yerevan date decides
    assert previous_month(datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc)) == "2026-10"
    assert previous_month(datetime(2026, 10, 31, 19, 30, tzinfo=timezone.utc)) == "2026-09"


def test_due_on_the_first_in_the_morning():
    assert monthly_due(state(), yv(11, 1, 10)) == "2026-10"


def test_not_due_when_that_month_was_already_sent():
    s = state()
    s.monthly_last = "2026-10"
    assert monthly_due(s, yv(11, 1, 10)) is None
    assert monthly_due(s, yv(11, 20, 10)) is None
    assert monthly_due(s, yv(12, 1, 10)) == "2026-11"


def test_not_due_at_night_but_catches_up_later_the_same_day():
    assert monthly_due(state(), yv(11, 1, 3)) is None
    assert monthly_due(state(), yv(11, 1, 23)) is None
    assert monthly_due(state(), yv(11, 1, 9)) == "2026-10"


def test_catches_up_in_the_middle_of_the_month():
    assert monthly_due(state(), yv(11, 15, 14)) == "2026-10"


def test_pending_ignores_the_time_of_day():
    assert month_pending(state(), yv(11, 1, 3)) == "2026-10"


# --- content --------------------------------------------------------------------------------

def test_month_is_bounded_in_yerevan_time():
    log = [ck(yv(10, 1, 0)),                                 # 00:00 Yerevan on 1 Oct: in
           ck(yv(10, 31, 23)),                               # 23:00 on 31 Oct: in
           ck(yv(11, 1, 0)),                                 # 1 Nov: out
           ck(datetime(2026, 9, 30, 20, 30, tzinfo=timezone.utc))]   # 00:30 on 1 Oct Yerevan: in (UTC says Sept)
    text = build_monthly(state(), CONFIG, log, "2026-10")
    assert "Чекинов: 3 " in text


def test_december_rolls_into_january():
    log = [ck(yv(12, 31, 22)), ck(yv(1, 1, 1, year=2027))]
    assert "Чекинов: 1 " in build_monthly(state(), CONFIG, log, "2026-12")


def test_none_when_the_month_has_no_checkins():
    assert build_monthly(state(), CONFIG, [ck(yv(9, 5))], "2026-10") is None


def test_headline_numbers_and_title():
    log = [ck(yv(10, 3), user="a"), ck(yv(10, 4), user="a", venue=2), ck(yv(10, 5), user="b", venue=OTHER_VENUE)]
    text = build_monthly(state(), CONFIG, log, "2026-10")
    assert text.startswith("📊 <b>Итоги октября</b>")
    assert "Чекинов: 3 · людей: 2 · мест: 3" in text


def test_top_places_use_short_names_then_venue_names_and_ties_break_by_name():
    log = ([ck(yv(10, 3), venue=1)] * 3 + [ck(yv(10, 3), venue=2)] * 2 + [ck(yv(10, 3), venue=OTHER_VENUE)] * 2
           + [ck(yv(10, 3), venue=777)])
    text = build_monthly(state(), CONFIG, log, "2026-10")
    top = text.split("<b>Больше всего чекинов</b>\n")[1].split("\n\n")[0].split("\n")
    assert top == ["1. Gargoyle — 3", "2. Beatles Pub — 2", "3. Some Pub — 2"]   # 777 is unknown and 4th


def test_places_of_one_venue_name_are_html_escaped():
    s = state()
    s.venues[str(OTHER_VENUE)] = VenueRec(name="Tom & <Jerry>", url="u")
    text = build_monthly(s, CONFIG, [ck(yv(10, 3), venue=OTHER_VENUE)], "2026-10")
    assert "1. Tom &amp; &lt;Jerry&gt; — 1" in text


def _rated(beer_id, ratings, brewery="Zagovor Brewery"):
    return [ck(yv(10, 7), beer_id=beer_id, rating=r, brewery=brewery) for r in ratings]


def test_best_beers_need_three_rated_checkins_and_show_average_with_trimmed_brewery():
    log = (_rated(10, [4.5, 4.0, 4.0, 5.0]) + _rated(11, [4.0, 4.0, 4.5]) + _rated(12, [3.0, 3.0, 3.0])
           + _rated(13, [5.0, 5.0]))   # 13 has only two: out while three others qualify
    text = build_monthly(state(), CONFIG, log, "2026-10")
    best = text.split("<b>Лучшее пиво</b> · от 3 оценок\n")[1].split("\n\n")[0].split("\n")
    assert best == ["1. Alpha — Zagovor 4.38".replace(" Zagovor", " Zagovor") if False else
                    "1. Alpha — Zagovor · 4.38",
                    "2. Beta — Zagovor · 4.17",
                    "3. Gamma — Zagovor · 3.00"]


def test_best_beers_fall_back_to_two_checkins_when_fewer_than_three_qualify():
    log = _rated(10, [4.0, 4.0, 4.0]) + _rated(11, [4.5, 4.5]) + _rated(12, [3.5, 3.5]) + _rated(13, [5.0])
    text = build_monthly(state(), CONFIG, log, "2026-10")
    best = text.split("<b>Лучшее пиво</b> · от 2 оценок\n")[1].split("\n\n")[0].split("\n")
    assert [line.split(" — ")[0] for line in best] == ["1. Beta", "2. Alpha", "3. Gamma"]


def test_best_beers_ignore_unrated_and_unnamed_and_the_section_is_dropped_when_empty():
    log = [ck(yv(10, 7), beer_id=10, rating=None)] * 3 + _rated(999, [5.0, 5.0, 5.0])
    text = build_monthly(state(), CONFIG, log, "2026-10")
    assert "Лучшее пиво" not in text


def test_new_beers_are_counted_by_place_kind_inside_the_month_in_our_places_only():
    s = state()
    s.pairs = {
        "gargoyle": {"u:1": pair(yv(10, 2)), "u:2": pair(yv(10, 31, 23)), "u:3": pair(yv(11, 1, 1)),
                     "u:4": pair(yv(9, 30, 23)), "u:5": pair(None)},     # u:5: no event
        "beatles": {"u:1": pair(yv(10, 9))},                              # same beer, other bar: counted once
        "parma": {"n:a": pair(yv(10, 9)), "n:b": pair(yv(10, 10), notified_at=iso(yv(10, 11))),
                  "u:1": pair(yv(10, 12))},                               # the same beer in a shop: counted there too
        "gone": {"u:6": pair(yv(10, 9))},                                 # not in the config
    }
    text = build_monthly(s, CONFIG, [ck(yv(10, 3))], "2026-10")
    assert text.rstrip().endswith("Новых сортов: в барах 2 · в магазинах 3")


def test_silent_first_sightings_are_not_new_beers():
    """A source connected mid-month (SAS) has its whole catalogue as baseline: not news."""
    s = state()
    s.pairs = {"parma": {f"n:{i}": pair(None, notified_at="baseline") for i in range(50)},
               "gargoyle": {"u:1": pair(yv(10, 2), notified_at="baseline"),   # even if an event time was left on it
                            "u:2": pair(yv(10, 2), notified_at="suppressed"),
                            "u:3": pair(yv(10, 2), notified_at=iso(yv(10, 3)))}}
    text = build_monthly(s, CONFIG, [ck(yv(10, 3))], "2026-10")
    assert text.rstrip().endswith("Новых сортов: в барах 1 · в магазинах 0")


def test_a_side_without_new_beers_still_shows_zero():
    assert build_monthly(state(), CONFIG, [ck(yv(10, 3))], "2026-10").endswith(
        "Новых сортов: в барах 0 · в магазинах 0")


def test_message_stays_short():
    log = _rated(10, [4, 4, 4]) + _rated(11, [4, 4, 4]) + _rated(12, [4, 4, 4])
    s = state()
    s.pairs = {"gargoyle": {"u:1": pair(yv(10, 2))}}
    assert len([ln for ln in build_monthly(s, CONFIG, log, "2026-10").split("\n") if ln]) <= 12
