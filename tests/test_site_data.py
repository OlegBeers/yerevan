import json
from datetime import datetime, timedelta, timezone

from taps.config import Config, Place, Settings
from taps.site_data import build_site_data, write_site_data
from taps.state import PairRec, SourceRec, State, VenueRec
from taps.timeutil import iso

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)  # 16:00 in Yerevan
STARTED = "2026-09-20T10:00:00+00:00"

GARGOYLE = Place(id="gargoyle", name="Gargoyle Bar", kind="bar",
                 sources={"untappd_menu": {"slug": "gargoyle", "venue_id": 1},
                          "untappd_checkins": {"slug": "gargoyle", "venue_id": 1}})
DORS = Place(id="dors", name="Dors Craft Beer & Kitchen", kind="brewpub",
             sources={"untappd_checkins": {"slug": "dors", "venue_id": 2}})
BEER_CITY = Place(id="beer-city", name="Beer City", kind="shop", sources={"beercity": {}})
PARMA = Place(id="parma", name="Парма", kind="shop", sources={"parma": {}})
CONFIG = Config(places={p.id: p for p in (GARGOYLE, DORS, BEER_CITY, PARMA)},
                breweries=(), settings=Settings(hot_rating=3.8))


def ago(days: float) -> str:
    return iso(NOW - timedelta(days=days))


def pair(source: str, first_seen: str = STARTED, **kw) -> PairRec:
    info = {"source": source, "name": "Black Sails", **kw.pop("info", {})}
    return PairRec(first_seen=first_seen, last_seen=first_seen, info=info, **kw)


def state(pairs: dict, sources: dict | None = None, venues: dict | None = None) -> State:
    return State(started_at=STARTED, pairs=pairs, sources=sources or {}, venues=venues or {})


def keys(data: dict) -> set[tuple[str, str]]:
    return {(r["place_id"], r["beer_key"]) for r in data["rows"]}


def build(st: State) -> dict:
    return build_site_data(st, CONFIG, NOW)


def test_menu_row_visible_only_when_in_last_result():
    st = state({"gargoyle": {"u:1": pair("untappd_menu", last_in_result=True),
                             "u:2": pair("untappd_menu", last_in_result=False)}})
    assert keys(build(st)) == {("gargoyle", "u:1")}


def test_shop_row_hidden_when_out_of_stock():
    st = state({"parma": {"n:a": pair("parma", in_stock=True),
                          "n:b": pair("parma", in_stock=None),
                          "n:c": pair("parma", in_stock=False),
                          "n:d": pair("parma", in_stock=True, last_in_result=False)}})
    assert keys(build(st)) == {("parma", "n:a"), ("parma", "n:b")}


def test_beercity_pairs_from_partial_runs_after_last_full_are_visible():
    full = ago(1)
    st = state(
        {"beer-city": {
            "n:partial": pair("beercity", first_seen=ago(0.5), in_stock=True, last_in_result=False),
            "n:old": pair("beercity", first_seen=ago(3), in_stock=True, last_in_result=False),
            "n:partial-out": pair("beercity", first_seen=ago(0.5), in_stock=False, last_in_result=False),
        }},
        {"beercity:beer-city": SourceRec(last_ok=ago(0.5), last_full=full)},
    )
    assert keys(build(st)) == {("beer-city", "n:partial")}


def test_checkin_row_visible_21_days_after_checkin():
    st = state({"dors": {
        "u:20": pair("untappd_checkins", info={"checkin_at": ago(20), "serving": "Draft"}),
        "u:22": pair("untappd_checkins", info={"checkin_at": ago(22)}),
        "u:0": pair("untappd_checkins", info={"checkin_at": ago(0.1)}),
    }})
    rows = {r["beer_key"]: r for r in build(st)["rows"]}
    assert set(rows) == {"u:20", "u:0"}
    assert rows["u:20"]["seen_days_ago"] == 20
    assert rows["u:0"]["seen_days_ago"] == 0
    assert rows["u:20"]["badge"] == "checkin"
    assert rows["u:20"]["serving"] == "Draft" and rows["u:0"]["serving"] is None


def test_checkin_row_shows_a_rating_cached_via_backfill():
    """v1.1 §2: rules._backfill_checkin writes rating/style/abv/ibu into a check-in pair's own info
    (from a menu pair or the state.beers cache); site_data passes it through like any other row."""
    st = state({"dors": {"u:9": pair("untappd_checkins", info={
        "checkin_at": ago(1), "serving": "Draft", "rating": 3.82, "style": "Fruit Beer", "abv": 6.2, "ibu": 18})}})
    row = build(st)["rows"][0]
    assert (row["rating"], row["style"], row["abv"], row["ibu"]) == (3.82, "Fruit Beer", 6.2, 18)


def test_manual_row_visible_14_days_from_date():
    st = state({"gargoyle": {
        "n:379 hazy pale": pair("manual", info={"manual_date": "2026-09-27", "manual_by": "Аня"}),
        "n:old": pair("manual", info={"manual_date": "2026-09-25", "manual_by": "Аня"}),
    }})
    rows = build(st)["rows"]
    assert [r["beer_key"] for r in rows] == ["n:379 hazy pale"]
    assert rows[0]["by"] == "Аня" and rows[0]["badge"] == "manual" and rows[0]["seen_days_ago"] is None


def test_manual_row_hidden_when_not_in_the_last_manual_result():
    st = state({"gargoyle": {
        "n:379 hazy pale": pair("manual", last_in_result=False, info={"manual_date": "2026-09-27", "manual_by": "Аня"}),
    }})
    assert build(st)["rows"] == []


def test_hidden_rows_never_shown():
    st = state({
        "gargoyle": {"u:1": pair("untappd_menu", info={"hidden": True})},
        "parma": {"n:corona": pair("parma", in_stock=True, notified_at="suppressed", info={"hidden": True})},
    })
    assert build(st)["rows"] == []


def test_new_and_star_only_for_recent_real_notifications():
    st = state({"gargoyle": {
        "u:1": pair("untappd_menu", notified_at=ago(6), star=True),
        "u:2": pair("untappd_menu", notified_at=ago(6), star=False),
        "u:3": pair("untappd_menu", notified_at=ago(8), star=True),
        "u:4": pair("untappd_menu", notified_at="baseline", star=True),
        "u:5": pair("untappd_menu", notified_at="suppressed", star=True),
        "u:6": pair("untappd_menu", notified_at=None, event_at=ago(0.1), star=True),
    }})
    flags = {r["beer_key"]: (r["new"], r["star"]) for r in build(st)["rows"]}
    assert flags == {"u:1": (True, True), "u:2": (True, False), "u:3": (False, False),
                     "u:4": (False, False), "u:5": (False, False), "u:6": (False, False)}


def test_section_follows_place_kind():
    st = state({"gargoyle": {"u:1": pair("untappd_menu")},
                "dors": {"u:2": pair("untappd_checkins", info={"checkin_at": ago(1)})},
                "parma": {"n:x": pair("parma", in_stock=True)}})
    data = build(st)
    assert {r["place_id"]: r["section"] for r in data["rows"]} == {
        "gargoyle": "bars", "dors": "bars", "parma": "shops"}
    assert [(p["id"], p["kind"], p["section"]) for p in data["places"]] == [
        ("gargoyle", "bar", "bars"), ("dors", "brewpub", "bars"),
        ("beer-city", "shop", "shops"), ("parma", "shop", "shops")]


def test_pairs_of_unknown_places_are_skipped():
    st = state({"closed-bar": {"u:1": pair("untappd_menu")}})
    assert build(st)["rows"] == []


def test_row_carries_display_fields():
    info = {"name": "Celebrator", "brewery": "Ayinger", "style": "Doppelbock", "abv": 6.7, "ibu": 24,
            "rating": 3.76, "price_amd": 2300, "volume_ml": 330, "container": "bottle",
            "url": "https://untappd.com/b/ayinger-celebrator/4280"}
    st = state({"gargoyle": {"u:4280": pair("untappd_menu", first_seen="2026-09-24T21:30:00+00:00", info=info)}})
    assert build(st)["rows"] == [{
        "place_id": "gargoyle", "section": "bars", "beer_key": "u:4280", "group_key": "u:4280", "name": "Celebrator",
        "brewery": "Ayinger", "style": "Doppelbock", "abv": 6.7, "ibu": 24, "rating": 3.76,
        "price_amd": 2300, "volume_ml": 330, "container": "bottle",
        "url": "https://untappd.com/b/ayinger-celebrator/4280", "serving": None, "shop_url": None, "beer_logo": None,
        "badge": "menu", "since": "2026-09-25",  # 01:30 next day in Yerevan
        "seen_days_ago": None, "new": False, "star": False, "by": None,
    }]


def test_row_prefers_canonical_untappd_name_and_brewery_when_matched():
    """v1.2 beer identity: apply_shop_matches (run.py) stores the Untappd beer's own canonical
    name/brewery in info["u_name"]/["u_brewery"], separate from the shop's own info["name"]/
    ["brewery"] (which the digest and rules.py still use, code review) -- only the site row prefers
    the canonical identity, e.g. so it groups/displays under the same name as on Untappd."""
    info = {"name": "Chimay peres trappistes blue", "brewery": "Chimay",
            "u_name": "Chimay Grande Réserve (Blue)", "u_brewery": "Bières de Chimay"}
    st = state({"beer-city": {"n:chimay peres trappistes blue": pair("beercity", in_stock=True, info=info)}})
    row = build(st)["rows"][0]
    assert (row["name"], row["brewery"]) == ("Chimay Grande Réserve (Blue)", "Bières de Chimay")


def test_row_carries_beer_logo_and_shop_url():
    """A shop row matched to Untappd (v1.1 §3): url/logo are the Untappd beer's, shop_url is the
    shop's own product page, both filled by the search-match overlay onto the pair's own info."""
    info = {"name": "Corona Extra", "url": "https://untappd.com/b/corona-extra/1", "logo": "https://x/logo.jpg",
            "shop_url": "https://parma.am/en/product/product?slug=corona_1"}
    st = state({"parma": {"n:corona extra": pair("parma", in_stock=True, info=info)}})
    row = build(st)["rows"][0]
    assert (row["url"], row["beer_logo"], row["shop_url"]) == (
        "https://untappd.com/b/corona-extra/1", "https://x/logo.jpg",
        "https://parma.am/en/product/product?slug=corona_1")


def test_places_status():
    st = state({}, {
        "untappd_menu:gargoyle": SourceRec(last_ok=ago(0.2), menu_updated_at="2026-10-08T09:00:00+00:00"),
        "untappd_checkins:gargoyle": SourceRec(last_ok=ago(2)),
        "untappd_checkins:dors": SourceRec(last_ok=ago(3.5), fail_streak=4, last_error="network"),
        "parma:parma": SourceRec(fail_streak=1, last_error="http"),
    })
    places = {p["id"]: p for p in build(st)["places"]}
    assert places["gargoyle"] == {
        "id": "gargoyle", "name": "Gargoyle Bar", "kind": "bar", "section": "bars",
        "last_ok": ago(0.2), "menu_updated_at": "2026-10-08T09:00:00+00:00",
        "failing": False, "failing_days": 0,
        "logo": None, "verified": False, "untappd_url": None, "addresses": []}
    assert (places["dors"]["failing"], places["dors"]["failing_days"]) == (True, 3)
    assert places["dors"]["menu_updated_at"] is None
    # never succeeded: days counted from started_at
    assert (places["parma"]["last_ok"], places["parma"]["failing"], places["parma"]["failing_days"]) == (None, True, 20)
    assert places["beer-city"]["last_ok"] is None and places["beer-city"]["failing"] is False
    assert "beercity:beer-city" not in st.sources  # building data never creates source records


def test_tripped_source_shows_as_failing():
    st = state({}, {"untappd_menu:gargoyle": SourceRec(fail_streak=1, last_error="shrink", trip_streak=1)})
    places = {p["id"]: p for p in build(st)["places"]}
    assert places["gargoyle"]["failing"] is True


def test_top_level_fields():
    data = build(state({}))
    assert data["generated_at"] == "2026-10-10T12:00:00+00:00"
    assert data["started_at"] == STARTED
    assert data["hot_rating"] == 3.8


def test_write_site_data_is_compact_utf8(tmp_path):
    data = build(state({"parma": {"n:x": pair("parma", in_stock=True, info={"name": "Ծիրան Էյլ"})}}))
    path = tmp_path / "data.json"
    write_site_data(path, data)
    text = path.read_text(encoding="utf-8")
    assert "Ծիրան Էյլ" in text and "Парма" in text
    assert "\\u" not in text and ", " not in text and ": " not in text and "\n" not in text
    assert json.loads(text) == data


# --- v1.1: venue meta on places, and the venues list ------------------------------------

def test_place_carries_venue_logo_and_verified_from_state():
    st = state({}, venues={"1": VenueRec(name="Gargoyle Bar", url="https://untappd.com/v/gargoyle/1",
                                         logo="https://x/logo.jpg", verified=True)})
    places = {p["id"]: p for p in build(st)["places"]}
    assert places["gargoyle"] == {
        "logo": "https://x/logo.jpg", "verified": True, "untappd_url": "https://untappd.com/v/gargoyle/1",
        "id": "gargoyle", "name": "Gargoyle Bar", "kind": "bar", "section": "bars",
        "last_ok": None, "menu_updated_at": None, "failing": False, "failing_days": 0, "addresses": []}
    assert places["parma"] == {
        "logo": None, "verified": False, "untappd_url": None,
        "id": "parma", "name": "Парма", "kind": "shop", "section": "shops",
        "last_ok": None, "menu_updated_at": None, "failing": False, "failing_days": 0, "addresses": []}


def test_place_carries_addresses_of_a_multi_venue_place():
    ba = Place(id="beer-academy", name="Beer Academy", kind="brewpub", sources={"untappd_checkins": {
        "venues": [{"slug": "beer-academy", "venue_id": 1, "address": "Московян 8"},
                   {"slug": "beer-academy-ethnograph", "venue_id": 2, "address": "Абовяна 10"}],
    }})
    config = Config(places={"beer-academy": ba}, breweries=(), settings=Settings())
    places = {p["id"]: p for p in build_site_data(state({}), config, NOW)["places"]}
    assert places["beer-academy"]["addresses"] == ["Московян 8", "Абовяна 10"]


def test_venues_list_sorted_by_checkins_then_name():
    st = state({}, venues={
        "1": VenueRec(name="Gargoyle Bar", url="https://untappd.com/v/gargoyle/1", logo="https://x/g.jpg",
                     verified=True, checkins=[{"id": 1, "at": ago(1)}, {"id": 2, "at": ago(2)}]),
        "99": VenueRec(name="KER U SUS", url="https://untappd.com/v/ker-u-sus/99", city="Yerevan", country="Armenia",
                      checkins=[{"id": 3, "at": ago(1)}, {"id": 4, "at": ago(2)}, {"id": 5, "at": ago(3)}]),
        "50": VenueRec(name="Old Bar", url="https://untappd.com/v/old/50", city="Yerevan", country="Armenia",
                      checkins=[{"id": 6, "at": ago(70)}]),
    })
    venues = build(st)["venues"]
    assert [v["name"] for v in venues] == ["KER U SUS", "Gargoyle Bar"]   # Old Bar has no checkin within 60d
    ker = venues[0]
    assert ker == {
        "venue_id": 99, "name": "KER U SUS", "url": "https://untappd.com/v/ker-u-sus/99",
        "logo": None, "verified": False, "checkins_30d": 3, "last_checkin": ago(1),
        "tracked": False, "place_id": None,
    }
    gargoyle = venues[1]
    assert (gargoyle["venue_id"], gargoyle["checkins_30d"], gargoyle["tracked"], gargoyle["place_id"]) == (
        1, 2, True, "gargoyle")


def test_venues_list_ties_break_by_name():
    st = state({}, venues={
        "2": VenueRec(name="Beatles Pub", url="u2", city="Yerevan", country="Armenia", checkins=[{"id": 1, "at": ago(1)}]),
        "1": VenueRec(name="Ambient Bar", url="u1", city="Yerevan", country="Armenia", checkins=[{"id": 2, "at": ago(1)}]),
    })
    assert [v["name"] for v in build(st)["venues"]] == ["Ambient Bar", "Beatles Pub"]


def test_venues_list_excludes_untracked_venue_with_foreign_or_unknown_location():
    """v1.1 city check (§4): a venue not in places.yaml is hidden until known to be in Armenia."""
    st = state({}, venues={
        "500": VenueRec(name="Old Tbilisi Brewery", url="u1", country="Georgia",
                       checkins=[{"id": 1, "at": ago(1)}]),
        "501": VenueRec(name="Not Yet Checked", url="u2", checkins=[{"id": 2, "at": ago(1)}]),   # country=None
    })
    assert build(st)["venues"] == []


def test_venues_list_includes_tracked_venue_regardless_of_location():
    """Tracked places (in places.yaml) are always shown, location known or not."""
    st = state({}, venues={"1": VenueRec(name="Gargoyle Bar", url="u1", checkins=[{"id": 1, "at": ago(1)}])})
    venues = build(st)["venues"]
    assert [v["name"] for v in venues] == ["Gargoyle Bar"]
    assert venues[0]["tracked"] is True


def test_venues_list_excludes_venue_of_disabled_place_even_when_city_matches():
    """A disabled place's venue (known_venue_ids) must never appear as untracked, even if Untappd
    reports its city as Yerevan (e.g. a mislocated venue that was disabled for that reason)."""
    config = Config(places=CONFIG.places, breweries=(), settings=CONFIG.settings,
                     known_venue_ids=frozenset({777}))
    st = state({}, venues={
        "777": VenueRec(name="Dahook Beer House", url="u1", city="Yerevan", country="Armenia",
                        checkins=[{"id": 1, "at": ago(1)}]),
        "99": VenueRec(name="KER U SUS", url="u2", city="Yerevan", country="Armenia",
                      checkins=[{"id": 2, "at": ago(1)}]),
    })
    data = build_site_data(st, config, NOW)
    names = {v["name"] for v in data["venues"]}
    assert "Dahook Beer House" not in names
    assert "KER U SUS" in names  # a genuinely unmatched Yerevan venue still shows as untracked
    gargoyle_data = build(state({}, venues={"1": VenueRec(name="Gargoyle Bar", url="u1",
                                                          checkins=[{"id": 1, "at": ago(1)}])}))["venues"]
    assert gargoyle_data[0]["tracked"] is True  # enabled-place venue still shows as tracked


def test_group_key_joins_shop_rows_matched_to_the_same_untappd_beer():
    """Search-matched shop beers keep their n: pair key (notification identity) but group with the bar's u: row."""
    url = "https://untappd.com/b/bitburger-brauerei-bitburger-premium-pils/17252"
    st = state({"gargoyle": {"u:17252": pair("untappd_menu", info={"url": url})},
                "beer-city": {"n:bitburger premium pils": pair("beercity", in_stock=True, info={"url": url})}})
    groups = {(r["place_id"], r["beer_key"]): r["group_key"] for r in build(st)["rows"]}
    assert groups == {("gargoyle", "u:17252"): "u:17252", ("beer-city", "n:bitburger premium pils"): "u:17252"}


def test_group_key_ignores_yerevan_city_glass_bottle_suffix():
    st = state({"beer-city": {"n:dargett apricot ale": pair("beercity", in_stock=True)},
                "parma": {"n:dargett apricot ale g b": pair("parma", in_stock=True)}})
    assert {r["group_key"] for r in build(st)["rows"]} == {"n:dargett apricot ale"}


def test_group_key_keeps_unmatched_shop_key_and_ignores_shop_page_urls():
    st = state({"parma": {"n:corona extra": pair("parma", in_stock=True,
                                                info={"url": "https://parma.am/en/product/product?slug=corona_1"})}})
    assert build(st)["rows"][0]["group_key"] == "n:corona extra"
