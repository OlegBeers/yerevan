import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from taps.config import Config, Place, Settings
from taps.digest import (
    build_digest, drop_stale_events, is_due, mark_sent, pending_brewery, pending_pairs, rollback,
)
from taps.state import BreweryNewRec, DigestRec, PairRec, State
from taps.timeutil import YEREVAN, iso, to_yerevan

NBSP = " "


def yv(day: int, hour: int, minute: int = 0, month: int = 9) -> datetime:
    """Yerevan wall-clock time as aware UTC."""
    return datetime(2026, month, day, hour, minute, tzinfo=YEREVAN).astimezone(timezone.utc)


NOW = yv(24, 18, 17)          # Thursday 24 Sep 2026, 18:17 Yerevan
SETTINGS = Settings()


def place(pid, name, kind, sources):
    return Place(id=pid, name=name, kind=kind, sources=sources)


CONFIG = Config(
    places={
        "gargoyle": place("gargoyle", "Gargoyle Bar", "bar", {"untappd_menu": {"slug": "g", "venue_id": 1}}),
        "beatles": place("beatles", "Beatles Pub", "bar", {"untappd_menu": {"slug": "b", "venue_id": 2}}),
        "dors": replace(place("dors", "Dors Craft Beer & Kitchen", "brewpub", {"untappd_checkins": {"slug": "d", "venue_id": 3}}),
                      short_name="Dors"),
        "tap-station": place("tap-station", "Tap Station", "bar", {"untappd_checkins": {"slug": "t", "venue_id": 4}}),
        "beer-city": place("beer-city", "Beer City", "shop", {"beercity": {}}),
        "parma": place("parma", "Parma", "shop", {"parma": {}}),
        "houl": place("houl", "Houl", "shop", {"untappd_checkins": {"slug": "houl", "venue_id": 5}}),
    },
    breweries=(),
    settings=SETTINGS,
)


def pair(event_at, notified_at=None, star=False, **info):
    seen = iso(event_at) if event_at else iso(NOW)
    return PairRec(first_seen=seen, last_seen=seen, event_at=iso(event_at) if event_at else None,
                   star=star, notified_at=notified_at, info=info)


def new_state(**kw) -> State:
    return State(started_at=iso(datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)), **kw)


def full_state() -> State:
    return new_state(
        pairs={
            "gargoyle": {
                "u:1": pair(yv(24, 10), kind="menu", brewery="Ayinger Privatbrauerei", name="Celebrator",
                            style="Doppelbock", abv=6.7, rating=3.76, price_amd=2300),
                "u:2": pair(yv(24, 12), star=True, kind="menu", brewery="Zagovor", name="Black Sails",
                            style="Imperial Stout", abv=11.0, rating=4.12),
                "u:3": pair(yv(24, 11), kind="menu", brewery="A&B", name="Tom & <Jerry>", style="Sour", rating=3.75),
                "u:4": pair(yv(20, 10), notified_at="baseline", kind="menu", name="Old Beer"),
                "u:5": pair(yv(23, 10), notified_at=iso(yv(23, 18)), kind="menu", name="Sent Yesterday"),
                "u:6": pair(None, kind="menu", name="Never An Event"),
            },
            "beatles": {"u:7": pair(yv(24, 9), kind="menu", name="Mystery Lager", rating=3.74)},
            "dors": {"u:8": pair(yv(22, 20), kind="checkin", source="untappd_checkins", brewery="Dors",
                                 name="Smoked Porter", serving="Draft", checkin_at=iso(yv(22, 20)))},
            "tap-station": {"n:379 hazy pale": pair(yv(24, 14), kind="manual", brewery="379", name="Hazy Pale",
                                                    manual_id="tap-station|2026-09-24|Аня", manual_by="Аня")},
            "beer-city": {"n:konix cassis ruby": pair(yv(24, 10), star=True, kind="shop", brewery="Konix",
                                                      name="Cassis Ruby", volume_ml=450, container="can",
                                                      price_amd=1900)},
            "parma": {"n:konix cassis ruby": pair(yv(24, 11), kind="shop", brewery="Konix", name="Cassis Ruby",
                                                  volume_ml=330, container="bottle", price_amd=2100)},
        },
        brewery_new={"u:10": BreweryNewRec(brewery_id=265165, found_at=iso(yv(24, 9)), star=True,
                                           info={"name": "DDH NEIPA", "brewery": "Dargett", "abv": 6.5})},
    )


EXPECTED = (
    "🍺 <b>Новое в Ереване</b> · чт, 24 сен\n"
    "\n"
    "🍻 <b>Бары</b>\n"
    "\n"
    "<b>Gargoyle Bar</b>\n"
    f"• Black Sails — Zagovor{NBSP}·{NBSP}Imperial{NBSP}Stout\n"
    f"• Celebrator — Ayinger{NBSP}Privatbrauerei{NBSP}·{NBSP}Doppelbock\n"
    f"• Tom &amp; &lt;Jerry&gt; — A&amp;B{NBSP}·{NBSP}Sour\n"
    "\n"
    "<b>Beatles Pub</b>\n"
    "• Mystery Lager\n"
    "\n"
    "<b>Dors</b> · по чекинам\n"
    "• Smoked Porter\n"
    "\n"
    "<b>Tap Station</b> · со слов: Аня\n"
    "• Hazy Pale — 379\n"
    "\n"
    "<b>Новые сорта пивоварен</b> · где наливают — пока неизвестно\n"
    "• DDH NEIPA — Dargett\n"
    "\n"
    "──────────\n"
    "\n"
    "🛒 <b>Магазины</b>\n"
    "\n"
    "<b>Beer City</b>\n"
    "• Cassis Ruby — Konix · ещё в Parma"
)


def test_build_digest_full_text():
    d = build_digest(full_state(), CONFIG, SETTINGS, NOW)
    assert d.html == EXPECTED
    assert (d.lines_total, d.lines_shown) == (8, 8)
    assert sorted(d.pairs) == sorted([
        ("gargoyle", "u:1"), ("gargoyle", "u:2"), ("gargoyle", "u:3"), ("beatles", "u:7"), ("dors", "u:8"),
        ("tap-station", "n:379 hazy pale"), ("beer-city", "n:konix cassis ruby"), ("parma", "n:konix cassis ruby"),
    ])
    assert d.brewery_keys == ["u:10"]
    assert d.manual_ids == ["tap-station|2026-09-24|Аня"]
    assert d.to_admin is True


def test_digest_text_has_emoji_only_in_the_title_and_the_two_section_headers():
    d = build_digest(full_state(), CONFIG, SETTINGS, NOW)
    assert d.html.count("🍺") == 1
    assert "🍻 <b>Бары</b>" in d.html and "🛒 <b>Магазины</b>" in d.html
    assert not any(ch in d.html for ch in "⭐🔥✅👀✍🏭")


def test_no_rating_abv_price_serving_or_seen_text_anywhere_in_the_digest():
    d = build_digest(full_state(), CONFIG, SETTINGS, NOW)
    for forbidden in ("֏", "%", "видели", "розлив", "л банка", "л бутылка", "<b>4.12</b>", "<b>3.76</b>"):
        assert forbidden not in d.html


def test_a_shop_pair_matched_to_untappd_shows_the_canonical_name_and_brewery():
    s = new_state(pairs={"beer-city": {"n:x": pair(yv(24, 10), kind="shop", name="Shop Name", brewery="Shop Brew",
                                                   u_name="Rise Of The Zombies", u_brewery="Plan B Brewery",
                                                   style="Pale Ale - American", abv=6.3, rating=3.87, volume_ml=330,
                                                   container="can", price_amd=2700)}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert f"• Rise Of The Zombies — Plan{NBSP}B{NBSP}·{NBSP}Pale{NBSP}Ale" in d.html
    assert "Shop Name" not in d.html


def test_the_brewery_is_dropped_when_it_is_the_place_itself():
    s = new_state(pairs={"dors": {"u:8": pair(yv(24, 9), kind="checkin", brewery="DORS", name="Pils", serving="Draft",
                                             checkin_at=iso(yv(24, 9)))},
                         "tap-station": {"n:x": pair(yv(24, 9), kind="manual", brewery="Dors", name="X", manual_by="Аня")}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "\n• Pils\n" in d.html                              # brewery == place: dropped, no style either
    assert "\n• X — Dors" in d.html                            # a brewery that is another place's name stays
    assert "Craft Beer" not in d.html


def test_pending_lists():
    s = full_state()
    assert ("gargoyle", "u:4") not in pending_pairs(s)      # baseline
    assert ("gargoyle", "u:5") not in pending_pairs(s)      # already sent
    assert ("gargoyle", "u:6") not in pending_pairs(s)      # no event_at
    assert len(pending_pairs(s)) == 8
    assert pending_brewery(s) == ["u:10"]


def test_shop_checkin_goes_to_the_shops_block():
    """v1.1: a check-in at a shop (e.g. Houl) belongs in Магазины, not Бары."""
    s = new_state(pairs={"houl": {"u:9": pair(yv(24, 9), kind="checkin", name="Stout", serving="Bottle",
                                              checkin_at=iso(yv(24, 9)))}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    shops_block = d.html.split("Магазины</b>", 1)[1]
    assert "Stout" in shops_block and "по чекинам" in shops_block
    assert "Бары</b>" not in d.html


def test_bar_and_shop_checkins_dont_interleave_blocks():
    """I-2: a manual entry at a bar + a shop check-in (Houl) + a bar check-in + a shop item must not
    interleave Бары/Магазины — all bars first, then all shops, each header exactly once."""
    s = new_state(pairs={
        "tap-station": {"n:379 hazy pale": pair(yv(24, 14), kind="manual", brewery="379", name="Hazy Pale",
                                                manual_by="Аня")},
        "houl": {"u:9": pair(yv(24, 9), kind="checkin", name="Stout", serving="Bottle",
                             checkin_at=iso(yv(24, 9)))},
        "dors": {"u:8": pair(yv(22, 20), kind="checkin", brewery="Dors", name="Smoked Porter",
                             serving="Draft", checkin_at=iso(yv(22, 20)))},
        "beer-city": {"n:x": pair(yv(24, 10), kind="shop", name="X")},
    })
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert d.html.count("Бары</b>") == 1
    assert d.html.count("Магазины</b>") == 1
    bars_pos, shops_pos = d.html.index("Бары</b>"), d.html.index("Магазины</b>")
    assert bars_pos < shops_pos
    assert d.html.index("Stout") > shops_pos
    assert d.html.index("X") > shops_pos
    assert d.html.index("Smoked Porter") < shops_pos
    assert d.html.index("Hazy Pale") < shops_pos


def test_to_admin_false_after_preview_digests():
    s = full_state()
    s.digest.sent_count = 2
    assert build_digest(s, CONFIG, SETTINGS, NOW).to_admin is False


def test_none_when_nothing_pending():
    s = new_state(pairs={"gargoyle": {"u:4": pair(yv(20, 10), notified_at="baseline", kind="menu", name="X")}})
    assert build_digest(s, CONFIG, SETTINGS, NOW) is None
    # pending only at a place no longer in config: nothing to show
    s = new_state(pairs={"closed-bar": {"u:1": pair(yv(24, 10), kind="menu", name="X")}})
    assert build_digest(s, CONFIG, SETTINGS, NOW) is None


def test_drop_stale_events():
    s = new_state(
        pairs={"gargoyle": {
            "u:1": pair(yv(21, 17), kind="menu", name="Stale"),          # 3 days 1h17m old
            "u:2": pair(yv(22, 10), kind="menu", name="Fresh"),          # 2 days 8h old
            "u:3": pair(yv(1, 10), notified_at="baseline", kind="menu", name="Baseline"),
        }},
        brewery_new={
            "u:10": BreweryNewRec(brewery_id=1, found_at=iso(yv(20, 10))),
            "u:11": BreweryNewRec(brewery_id=1, found_at=iso(yv(24, 10))),
        },
    )
    assert drop_stale_events(s, NOW) == 2
    g = s.pairs["gargoyle"]
    assert g["u:1"].notified_at == iso(NOW)
    assert g["u:2"].notified_at is None
    assert g["u:3"].notified_at == "baseline"
    assert s.brewery_new["u:10"].notified_at == iso(NOW)
    assert s.brewery_new["u:11"].notified_at is None
    assert drop_stale_events(s, NOW) == 0


def test_drop_stale_events_uses_manual_date_not_merge_time():
    s = new_state(pairs={"tap-station": {
        "n:x": pair(yv(24, 17), kind="manual", manual_date="2026-09-20", name="X"),   # merged an hour ago
    }})
    assert drop_stale_events(s, NOW) == 1
    assert s.pairs["tap-station"]["n:x"].notified_at == iso(NOW)


def test_drop_stale_events_keeps_a_manual_entry_dated_exactly_three_days_ago():
    """Same calendar-day rule as rules.MANUAL_EVENT_DAYS: recorded as an event, so not dropped in the same run."""
    s = new_state(pairs={"tap-station": {
        "n:x": pair(yv(24, 17), kind="manual", manual_date="2026-09-21", name="X"),   # 3 days ago, 18:17 today
        "n:y": pair(yv(24, 17), kind="manual", manual_date="2026-09-20", name="Y"),   # 4 days ago
    }})
    assert drop_stale_events(s, NOW) == 1
    assert s.pairs["tap-station"]["n:x"].notified_at is None
    assert s.pairs["tap-station"]["n:y"].notified_at == iso(NOW)


def due_state(event_at=None, found_at=None, last_sent_at=None, last_sent_date=None) -> State:
    s = new_state(digest=DigestRec(last_sent_date=last_sent_date, last_sent_at=last_sent_at and iso(last_sent_at)))
    if event_at:
        s.pairs["gargoyle"] = {"u:1": pair(event_at, kind="menu", name="X")}
    if found_at:
        s.brewery_new["u:10"] = BreweryNewRec(brewery_id=1, found_at=iso(found_at))
    return s


def test_is_due_evening_with_pending():
    assert is_due(due_state(event_at=yv(24, 10, 17)), SETTINGS, yv(24, 18, 17)) is True


def test_is_due_morning_waits_for_evening():
    assert is_due(due_state(event_at=yv(24, 10, 17)), SETTINGS, yv(24, 10, 17)) is False
    # found yesterday after 17:00: still waits for this evening
    assert is_due(due_state(event_at=yv(23, 17, 30)), SETTINGS, yv(24, 10, 17)) is False


def test_is_due_morning_catches_up_missed_evening():
    s = due_state(event_at=yv(23, 10, 17), last_sent_at=yv(22, 18, 17), last_sent_date="2026-09-22")
    assert is_due(s, SETTINGS, yv(24, 10, 17)) is True
    assert is_due(due_state(found_at=yv(23, 10, 17)), SETTINGS, yv(24, 10, 17)) is True   # brewery event


def test_is_due_already_sent_today():
    assert is_due(due_state(event_at=yv(24, 10), last_sent_date="2026-09-24"), SETTINGS, yv(24, 18, 17)) is False


def test_is_due_twenty_hour_gap():
    old = yv(23, 10, 17)
    s = due_state(event_at=old, last_sent_at=yv(23, 18, 17), last_sent_date="2026-09-23")   # 16h ago
    assert is_due(s, SETTINGS, yv(24, 10, 17)) is False
    s = due_state(event_at=old, last_sent_at=yv(23, 13, 0), last_sent_date="2026-09-23")    # 21h17m ago
    assert is_due(s, SETTINGS, yv(24, 10, 17)) is True


def test_is_due_quiet_hours():
    s = due_state(event_at=yv(23, 10))
    assert is_due(s, SETTINGS, yv(24, 23, 30)) is False
    assert is_due(s, SETTINGS, yv(24, 8, 59)) is False
    assert is_due(s, SETTINGS, yv(24, 9, 0)) is True


def test_is_due_manual_entry_waits_for_evening_even_with_an_older_date():
    """A hand-entered board dated yesterday (midnight) must not trigger the daytime catch-up send:
    the catch-up clock is when the entry reached the bot, not the date written in corrections.yaml."""
    manual_date = (to_yerevan(NOW).date() - timedelta(days=1)).isoformat()
    s = new_state(pairs={"ferment": {
        "n:x": pair(yv(24, 8, 30), kind="manual", manual_date=manual_date, name="X"),   # merged this morning
    }})
    assert is_due(s, SETTINGS, yv(24, 9, 30)) is False
    assert is_due(s, SETTINGS, yv(24, 18, 17)) is True


def test_is_due_manual_entry_merged_before_yesterday_evening_still_catches_up():
    manual_date = (to_yerevan(NOW).date() - timedelta(days=2)).isoformat()
    s = new_state(pairs={"ferment": {
        "n:x": pair(yv(23, 10, 17), kind="manual", manual_date=manual_date, name="X"),   # merged yesterday morning
    }}, digest=DigestRec(last_sent_date="2026-09-22", last_sent_at=iso(yv(22, 18, 17))))
    assert is_due(s, SETTINGS, yv(24, 10, 17)) is True


def test_is_due_nothing_pending():
    s = due_state()
    s.pairs["gargoyle"] = {"u:1": pair(yv(23, 10), notified_at="baseline", kind="menu", name="X")}
    assert is_due(s, SETTINGS, yv(24, 18, 17)) is False


def test_mark_sent_and_rollback():
    s = new_state(pairs={
        "gargoyle": {f"u:{100 + i}": pair(yv(24, 10, i), kind="menu", name=f"Bar {i:02d}") for i in range(5)},
        "beatles": {f"u:{200 + i}": pair(yv(24, 10, i), kind="menu", name=f"Beat {i:02d}") for i in range(5)},
        "dors": {f"u:{300 + i}": pair(yv(24, 10, i), kind="menu", name=f"Dor {i:02d}") for i in range(5)},
        "tap-station": {
            "n:m1": pair(yv(24, 14), kind="manual", name="X", manual_id="m1", manual_by="Аня"),
            **{f"u:{400 + i}": pair(yv(24, 10, i), kind="menu", name=f"Tap {i:02d}") for i in range(2)},
        },
    })
    s.brewery_new["u:10"] = BreweryNewRec(brewery_id=1, found_at=iso(yv(24, 9)), info={"name": "N"})
    s.digest = DigestRec(last_sent_date="2026-09-22", last_sent_at=iso(yv(22, 18)), sent_count=3)
    s.announced_manual = ["old"]
    before = copy.deepcopy(s.to_dict())
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert d.lines_shown == 15 and len(d.pairs) == 18   # 17 menu beers + 1 manual, capped at digest_max_lines

    later = yv(25, 0, 30)          # past midnight Yerevan: the date is the Yerevan one
    mark = mark_sent(s, d, later)
    assert s.digest == DigestRec(last_sent_date="2026-09-25", last_sent_at=iso(later), sent_count=4)
    assert all(s.pairs[p][k].notified_at == iso(later) for p, k in d.pairs)   # hidden ones too
    assert s.brewery_new["u:10"].notified_at == iso(later)
    assert s.announced_manual == ["old", "m1"]
    assert pending_pairs(s) == [] and pending_brewery(s) == []

    rollback(s, mark)
    assert s.to_dict() == before


def test_a_whole_board_entered_by_hand_is_one_line_about_the_place():
    st = full_state()
    st.pairs["tap-station"].update({
        f"n:beer {i}": pair(yv(24, 14), kind="manual", brewery="B", name=f"Beer {i}",
                            manual_id="tap-station|2026-09-24|Instagram бара", manual_by="Instagram бара")
        for i in range(6)})
    d = build_digest(st, CONFIG, SETTINGS, NOW)
    assert "\n<b>Tap Station</b> · обновился список, 7 позиций — на сайте" in d.html
    assert "Beer 0" not in d.html and "Hazy Pale" not in d.html
    assert d.html.count("обновился список") == 1
    # every entry is still marked as announced
    assert ("tap-station", "n:beer 5") in d.pairs and "tap-station|2026-09-24|Instagram бара" in d.manual_ids


def test_a_few_manual_entries_are_listed_one_by_one():
    d = build_digest(full_state(), CONFIG, SETTINGS, NOW)
    assert "Hazy Pale" in d.html and "обновился список" not in d.html


def test_manual_group_header_joins_distinct_contributors():
    s = new_state(pairs={"tap-station": {
        "n:x": pair(yv(24, 14), kind="manual", name="X", manual_by="Аня"),
        "n:y": pair(yv(24, 15), kind="manual", name="Y", manual_by="Боря"),
    }})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "<b>Tap Station</b> · со слов: Аня, Боря" in d.html


def test_a_beer_with_several_servings_is_announced_by_its_first_one_as_before():
    """The digest stays keyed by pair and unchanged for single-serving beers; extra servings show on the site only."""
    servings = [{"container": "draft", "price_amd": 2300, "volume_ml": 500},
                {"container": "bottle", "price_amd": 1500, "volume_ml": 330}]

    def digest_with(**extra):
        st = new_state(pairs={
            "gargoyle": {"u:1": pair(yv(24, 10), kind="menu", brewery="Zagovor", name="Black Sails", rating=3.9,
                                     container="draft", price_amd=2300, volume_ml=500, **extra)},
            "tap-station": {"n:379 hazy pale": pair(yv(24, 14), kind="manual", brewery="379", name="Hazy Pale",
                                                    container="draft", price_amd=1900, manual_id="m1",
                                                    manual_by="Аня", **extra)},
        })
        return build_digest(st, CONFIG, SETTINGS, NOW)

    several, single = digest_with(servings=servings), digest_with()
    assert several.html == single.html
    assert several.html.count("Black Sails") == 1
    assert "• Black Sails — Zagovor" in several.html
    assert (several.lines_total, several.pairs, several.manual_ids) == (2, single.pairs, ["m1"])


def test_a_pair_of_merged_entries_marks_every_entry_id_as_announced():
    s = new_state(pairs={"tap-station": {"n:x": pair(yv(24, 14), kind="manual", name="X", manual_id="m1",
                                                    manual_ids=["m1", "m2"], manual_by="Аня")}})
    digest = build_digest(s, CONFIG, SETTINGS, NOW)
    assert digest.manual_ids == ["m1", "m2"]
    mark_sent(s, digest, yv(24, 18, 30))
    assert s.announced_manual == ["m1", "m2"]


def test_bars_and_shops_are_split_by_a_rule_and_a_single_block_has_none():
    d = build_digest(full_state(), CONFIG, SETTINGS, NOW)
    assert d.html.count("──────────") == 1
    assert d.html.index("🍻 <b>Бары</b>") < d.html.index("──────────") < d.html.index("🛒 <b>Магазины</b>")
    only_bars = new_state(pairs={"gargoyle": {"u:1": pair(yv(24, 10), kind="menu", name="X", brewery="Y")}})
    assert "──────────" not in build_digest(only_bars, CONFIG, SETTINGS, NOW).html


# --- name/brewery shortening ------------------------------------------------------------------------


def test_a_comma_listing_parenthetical_is_dropped_from_the_name():
    s = new_state(pairs={"gargoyle": {"u:1": pair(yv(24, 10), kind="menu", brewery="Zagovor",
                                                  name="Carried Away (Apricot, Pear, Quince, Cardamom)")}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "• Carried Away — Zagovor" in d.html
    assert "Apricot" not in d.html


@pytest.mark.parametrize("name", ["X (Seven Sins)", "X (Квас Тарас)"])
def test_a_parenthetical_without_a_comma_stays_in_the_name(name):
    s = new_state(pairs={"gargoyle": {"u:1": pair(yv(24, 10), kind="menu", brewery="Zagovor", name=name)}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert f"• {name} — Zagovor" in d.html


@pytest.mark.parametrize("brewery, expected", [
    ("Plan B Brewery", f"Plan{NBSP}B"),
    (f"Gletcher Brewery (Глетчер)", "Gletcher"),
    ("Moscow Brewing Company", f"Moscow{NBSP}Brewing{NBSP}Company"),
    ("Brouwerij De Brabandere", f"Brouwerij{NBSP}De{NBSP}Brabandere"),
    ("Zagovor", "Zagovor"),
])
def test_brewery_shortening_rules(brewery, expected):
    s = new_state(pairs={"gargoyle": {"u:1": pair(yv(24, 10), kind="menu", brewery=brewery, name="X")}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert f"• X — {expected}" in d.html


# --- style family ------------------------------------------------------------------------------------


@pytest.mark.parametrize("style, expected", [
    ("IPA - Imperial / Double", "DIPA"),
    ("IPA - Imperial / Double - New England / Hazy", "DIPA"),
    ("IPA - Imperial / Double - Black", "DIPA"),
    ("IPA - Triple", "TIPA"),
    ("IPA - New England / Hazy", "NEIPA"),
    ("Stout - Imperial / Double", "Imperial Stout"),
])
def test_style_prefix_table_mappings(style, expected):
    s = new_state(pairs={"gargoyle": {"u:1": pair(yv(24, 10), kind="menu", brewery="Zagovor", name="X", style=style)}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    expected_nbsp = expected.replace(" ", NBSP)
    assert f"• X — Zagovor{NBSP}·{NBSP}{expected_nbsp}" in d.html


@pytest.mark.parametrize("style, expected", [
    ("Wheat Beer - Hefeweizen", "Wheat Beer"),
    ("Sour - Fruited", "Sour"),
    ("Pale Ale - American", "Pale Ale"),
    ("Mead", "Mead"),
])
def test_style_family_fallback_cuts_before_the_first_dash(style, expected):
    s = new_state(pairs={"gargoyle": {"u:1": pair(yv(24, 10), kind="menu", brewery="Zagovor", name="X", style=style)}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    expected_nbsp = expected.replace(" ", NBSP)
    assert f"• X — Zagovor{NBSP}·{NBSP}{expected_nbsp}" in d.html


def test_style_is_omitted_when_unknown():
    s = new_state(pairs={"gargoyle": {"u:1": pair(yv(24, 10), kind="menu", brewery="Zagovor", name="X")}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "• X — Zagovor\n" in d.html or d.html.endswith("• X — Zagovor")


def test_style_shows_after_the_name_alone_when_brewery_is_dropped():
    s = new_state(pairs={"dors": {"u:8": pair(yv(24, 9), kind="checkin", brewery="Dors", name="Pils",
                                             style="Pilsner", checkin_at=iso(yv(24, 9)))}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "• Pils · Pilsner" in d.html
    assert f"Pils{NBSP}" not in d.html   # no dash tail, so no NBSP binding here


def test_style_tail_after_the_dash_never_has_a_breaking_space():
    s = new_state(pairs={"gargoyle": {"u:1": pair(yv(24, 10), kind="menu", brewery="Gletcher Brewery (Глетчер)",
                                                  name="X", style="Pale Ale - American")}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert f"• X — Gletcher{NBSP}·{NBSP}Pale{NBSP}Ale" in d.html
    tail = d.html.split("• X — ", 1)[1].split("\n", 1)[0]
    assert " " not in tail


# --- also / multi-place --------------------------------------------------------------------------------


def test_a_beer_seen_at_several_places_lists_the_others():
    s = new_state(pairs={
        "beer-city": {"n:x": pair(yv(24, 10), kind="shop", name="Cassis Ruby", brewery="Konix")},
        "parma": {"n:x": pair(yv(24, 11), kind="shop", name="Cassis Ruby", brewery="Konix")},
    })
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "• Cassis Ruby — Konix · ещё в Parma" in d.html
    assert d.html.count("Cassis Ruby") == 1


# --- grouping headers --------------------------------------------------------------------------------


def test_checkin_group_header_says_po_chekinam():
    s = new_state(pairs={"dors": {"u:8": pair(yv(24, 9), kind="checkin", name="Pils", checkin_at=iso(yv(24, 9)))}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "<b>Dors</b> · по чекинам\n• Pils" in d.html


def test_menu_group_header_is_the_place_name_alone():
    s = new_state(pairs={"gargoyle": {"u:1": pair(yv(24, 10), kind="menu", brewery="Zagovor", name="X")}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "<b>Gargoyle Bar</b>\n• X — Zagovor" in d.html


def test_shop_group_header_is_the_place_name_alone():
    s = new_state(pairs={"beer-city": {"n:x": pair(yv(24, 10), kind="shop", name="X")}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "<b>Beer City</b>\n• X" in d.html


def test_brewery_new_group_header_and_bullet():
    s = new_state(brewery_new={"u:10": BreweryNewRec(brewery_id=1, found_at=iso(yv(24, 9)),
                                                      info={"name": "DDH NEIPA", "brewery": "Dargett"})})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "<b>Новые сорта пивоварен</b> · где наливают — пока неизвестно\n• DDH NEIPA — Dargett" in d.html


# --- per-group cap -------------------------------------------------------------------------------------


def test_per_group_cap_hides_the_tail_of_a_single_place_but_keeps_all_pairs():
    s = new_state(pairs={"gargoyle": {
        f"u:{100 + i}": pair(yv(24, 10, i), kind="menu", name=f"Bar {i:02d}", rating=5 - i * 0.1) for i in range(7)
    }})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert d.html.count("\n• Bar ") == 5
    assert "Bar 04" in d.html and "Bar 05" not in d.html
    assert "\n…и ещё 2 — на сайте" in d.html
    assert len(d.pairs) == 7
    assert (d.lines_total, d.lines_shown) == (6, 6)   # 5 bullets + 1 inline tail, well under the global cap


def test_per_group_cap_applies_to_the_brewery_new_group_too():
    s = new_state(brewery_new={
        f"u:{i}": BreweryNewRec(brewery_id=i, found_at=iso(yv(24, 9)), info={"name": f"Beer {i}", "brewery": "B"})
        for i in range(6)
    })
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert d.html.count("\n• Beer ") == 5
    assert "\n…и ещё 1 — на сайте" in d.html


# --- global cap -----------------------------------------------------------------------------------------


def test_global_cap_hides_a_trailing_group_entirely_and_appends_one_final_tail():
    s = new_state(pairs={
        "gargoyle": {f"u:{100 + i}": pair(yv(24, 10, i), kind="menu", name=f"Bar {i:02d}") for i in range(5)},
        "beatles": {f"u:{200 + i}": pair(yv(24, 10, i), kind="menu", name=f"Beat {i:02d}") for i in range(5)},
        "dors": {f"u:{300 + i}": pair(yv(24, 10, i), kind="menu", name=f"Dor {i:02d}") for i in range(5)},
        "tap-station": {f"u:{400 + i}": pair(yv(24, 10, i), kind="menu", name=f"Tap {i:02d}") for i in range(2)},
        "beer-city": {"n:shop": pair(yv(24, 10), kind="shop", name="Shop X")},
    })
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert (d.lines_total, d.lines_shown) == (18, 15)
    assert "Tap 00" not in d.html and "Tap 01" not in d.html
    assert "Магазины" not in d.html and "Shop X" not in d.html   # the trailing shop group is fully cut
    assert d.html.rstrip().endswith("…и ещё 3 — на сайте")
    assert len(d.pairs) == 18


def test_global_cap_final_tail_line_is_a_single_paragraph_after_the_body():
    s = new_state(pairs={pid: {f"u:{pid}-{i}": pair(yv(24, 10, i), kind="menu", name=f"{pid} {i}") for i in range(4)}
                         for pid in ("gargoyle", "beatles", "dors", "tap-station")})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert d.lines_total == 16 and d.lines_shown == 15
    assert d.html.endswith("\n\n…и ещё 1 — на сайте")


def test_is_due_something_found_after_the_morning_waits_for_the_next_morning():
    """Live settings (digest at 10:30): the morning run sends; a beer the evening run finds waits for the
    next morning instead of a second send slot, unless the morning run never happened."""
    from datetime import time
    live = Settings(digest_time=time(10, 30))
    assert is_due(due_state(event_at=yv(24, 18, 17)), live, yv(24, 18, 20)) is False
    assert is_due(due_state(event_at=yv(24, 10, 36)), live, yv(24, 10, 40)) is True     # the morning run's own find
    assert is_due(due_state(event_at=yv(23, 18, 17)), live, yv(24, 18, 20)) is True     # morning run missed
