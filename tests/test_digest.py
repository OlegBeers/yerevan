import copy
from datetime import datetime, timedelta, timezone

import pytest

from taps.config import Config, Place, Settings
from taps.digest import (
    build_digest, drop_stale_events, is_due, mark_sent, pending_brewery, pending_pairs, rollback,
)
from taps.state import BreweryNewRec, DigestRec, PairRec, State
from taps.timeutil import YEREVAN, iso, to_yerevan


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
        "dors": place("dors", "Dors Craft Beer & Kitchen", "brewpub", {"untappd_checkins": {"slug": "d", "venue_id": 3}}),
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


EXPECTED = """🍺 <b>Новое в Ереване</b> · чт, 24 сен

🍻 <b>БАРЫ</b>
<b>Gargoyle Bar</b> ✅
• ⭐ Zagovor — Black Sails · Imperial Stout 11% · 🔥4.12
• Ayinger Privatbrauerei — Celebrator · Doppelbock 6.7% · 🔥3.76 · 2300 ֏
• A&amp;B — Tom &amp; &lt;Jerry&gt; · Sour · 🔥3.75
<b>Beatles Pub</b> ✅
• Mystery Lager · 3.74
🏭 <b>Новые сорта пивоварен</b>
• ⭐ Dargett — DDH NEIPA · 6.5% (новый сорт в Untappd, где наливают — пока неизвестно)
👀 <b>Похоже, появилось</b>
• Dors — Smoked Porter · в Dors Craft Beer &amp; Kitchen, розлив, видели 2 дня назад
✍️ <b>Со слов</b>
• 379 — Hazy Pale · в Tap Station (от Аня)

🛒 <b>МАГАЗИНЫ</b>
<b>Beer City</b>
• ⭐ Konix — Cassis Ruby · 0.45 л банка · 1900 ֏ + ещё в Parma

<i>⭐ — возможно, впервые в Ереване (с тех пор, как следим, с 20.09)</i>"""


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


def test_pending_lists():
    s = full_state()
    assert ("gargoyle", "u:4") not in pending_pairs(s)      # baseline
    assert ("gargoyle", "u:5") not in pending_pairs(s)      # already sent
    assert ("gargoyle", "u:6") not in pending_pairs(s)      # no event_at
    assert len(pending_pairs(s)) == 8
    assert pending_brewery(s) == ["u:10"]


def test_checkin_seen_today_and_unknown_serving():
    s = new_state(pairs={"dors": {"u:8": pair(yv(24, 9), kind="checkin", name="Pils", serving=None,
                                             checkin_at=iso(yv(24, 9)))}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "• Pils · в Dors Craft Beer &amp; Kitchen, подача неизвестна, видели сегодня" in d.html


@pytest.mark.parametrize("days_ago,expected", [
    (0, "видели сегодня"), (1, "видели 1 день назад"), (2, "видели 2 дня назад"),
    (4, "видели 4 дня назад"), (5, "видели 5 дней назад"), (11, "видели 11 дней назад"),
])
def test_checkin_seen_ago_uses_correct_russian_plural(days_ago, expected):
    when = yv(24, 18) - timedelta(days=days_ago)
    s = new_state(pairs={"dors": {"u:8": pair(when, kind="checkin", name="Pils", serving="Draft",
                                              checkin_at=iso(when))}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert expected in d.html


@pytest.mark.parametrize("rating, expected", [(4.12, "🔥4.12"), (3.75, "🔥3.75"), (3.5, "3.50")])
def test_checkin_shows_a_cached_rating_with_the_hot_threshold(rating, expected):
    """v1.1 §2: a check-in row with a rating backfilled from state.beers (menu or the beer-page cache)
    shows it exactly like a menu row -- 🔥 only at/above hot_rating, else a plain number."""
    s = new_state(pairs={"dors": {"u:8": pair(yv(22, 20), kind="checkin", name="Smoked Porter", serving="Draft",
                                              checkin_at=iso(yv(22, 20)), rating=rating)}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert f"Smoked Porter · {expected} · в Dors" in d.html


def test_shop_checkin_goes_to_the_shops_block():
    """v1.1: a check-in at a shop (e.g. Houl) belongs in МАГАЗИНЫ, not БАРЫ."""
    s = new_state(pairs={"houl": {"u:9": pair(yv(24, 9), kind="checkin", name="Stout", serving="Bottle",
                                              checkin_at=iso(yv(24, 9)))}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    shops_block = d.html.split("МАГАЗИНЫ</b>", 1)[1]
    assert "Stout" in shops_block and "👀" in shops_block
    assert "БАРЫ</b>" not in d.html


def test_bar_and_shop_checkins_dont_interleave_blocks():
    """I-2: a manual entry at a bar + a shop check-in (Houl) + a bar check-in + a shop item must not
    interleave БАРЫ/МАГАЗИНЫ — all bars first, then all shops, each header exactly once."""
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
    assert d.html.count("БАРЫ</b>") == 1
    assert d.html.count("МАГАЗИНЫ</b>") == 1
    bars_pos, shops_pos = d.html.index("БАРЫ</b>"), d.html.index("МАГАЗИНЫ</b>")
    assert bars_pos < shops_pos
    assert d.html.index("Stout") > shops_pos
    assert d.html.index("X") > shops_pos
    assert d.html.index("Smoked Porter") < shops_pos
    assert d.html.index("Hazy Pale") < shops_pos


@pytest.mark.parametrize("serving, ru", [
    ("Bottle", "бутылка"), ("Can", "банка"), ("Taster", "дегустационный"), ("Cask", "из бочки"),
])
def test_serving_ru_translates_container_types(serving, ru):
    """M-2: a shop's own check-ins (e.g. Houl) report a container, not "розлив"."""
    s = new_state(pairs={"houl": {"u:9": pair(yv(24, 9), kind="checkin", name="Stout", serving=serving,
                                              checkin_at=iso(yv(24, 9)))}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert f"в Houl, {ru}, видели" in d.html


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


def cap_state(bars: int, shops: int) -> State:
    s = new_state(pairs={"gargoyle": {}, "beer-city": {}})
    for i in range(bars):
        s.pairs["gargoyle"][f"u:{100 + i}"] = pair(yv(24, 10, i), kind="menu", name=f"Bar {i:02d}")
    for i in range(shops):
        s.pairs["beer-city"][f"n:shop {i}"] = pair(yv(24, 10, i), kind="shop", name=f"Shop {i:02d}")
    return s


def test_cap_hides_tail_but_keeps_all_pairs():
    d = build_digest(cap_state(17, 0), CONFIG, SETTINGS, NOW)
    assert (d.lines_total, d.lines_shown) == (17, 15)
    assert d.html.count("\n• ") == 15
    assert "• Bar 14" in d.html and "Bar 15" not in d.html and "Bar 16" not in d.html
    assert d.html.endswith("• Bar 14\n\n…и ещё 2 — на сайте\n"
                           "<i>⭐ — возможно, впервые в Ереване (с тех пор, как следим, с 20.09)</i>")
    assert len(d.pairs) == 17


def test_cap_drops_empty_shop_block():
    d = build_digest(cap_state(15, 2), CONFIG, SETTINGS, NOW)
    assert "МАГАЗИНЫ" not in d.html and "Beer City" not in d.html
    assert "…и ещё 2 — на сайте" in d.html
    d = build_digest(cap_state(12, 5), CONFIG, SETTINGS, NOW)
    assert "• Shop 02" in d.html and "Shop 03" not in d.html


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
    s = cap_state(17, 0)
    s.pairs["tap-station"] = {"n:x": pair(yv(24, 14), kind="manual", name="X", manual_id="m1", manual_by="Аня")}
    s.brewery_new["u:10"] = BreweryNewRec(brewery_id=1, found_at=iso(yv(24, 9)), info={"name": "N"})
    s.digest = DigestRec(last_sent_date="2026-09-22", last_sent_at=iso(yv(22, 18)), sent_count=3)
    s.announced_manual = ["old"]
    before = copy.deepcopy(s.to_dict())
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert d.lines_shown == 15 and len(d.pairs) == 18

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
    assert "<b>Tap Station</b>: обновился список, 7 позиций (от Instagram бара, Аня) — на сайте" in d.html
    assert "Beer 0" not in d.html and "Hazy Pale" not in d.html
    assert d.html.count("✍️ <b>Со слов</b>") == 1
    # every entry is still marked as announced
    assert ("tap-station", "n:beer 5") in d.pairs and "tap-station|2026-09-24|Instagram бара" in d.manual_ids


def test_a_few_manual_entries_are_listed_one_by_one():
    d = build_digest(full_state(), CONFIG, SETTINGS, NOW)
    assert "Hazy Pale" in d.html and "обновился список" not in d.html
