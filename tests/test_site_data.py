import json
from datetime import datetime, timedelta, timezone

from taps.config import Config, Place, Settings
from taps.site_data import build_site_data, write_site_data
from taps.state import PairRec, ShopMatchRec, SourceRec, State, VenueRec
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
        "country": None,
        "badge": "menu", "since": "2026-09-25",  # 01:30 next day in Yerevan
        "since_at": "2026-09-24T21:30:00+00:00",
        "seen_days_ago": None, "new": False, "star": False, "by": None, "match_weak": False,
        "shop_name": None, "shop_brewery": None, "match_via": None,
    }]


def test_row_carries_the_country_the_shop_states():
    st = state({"beer-city": {"n:a": pair("beercity", in_stock=True, info={"country": "Ukraine"}),
                              "n:b": pair("beercity", in_stock=True)}})
    assert {r["beer_key"]: r["country"] for r in build(st)["rows"]} == {"n:a": "Ukraine", "n:b": None}


def test_row_guesses_a_style_from_the_name_of_a_shop_beer_with_no_untappd_behind_it():
    st = state({"beer-city": {"n:a": pair("beercity", in_stock=True, info={"name": "Bever pilsner"}),
                              "n:b": pair("beercity", in_stock=True, info={"name": "Bitburger"})}})
    rows = {r["beer_key"]: r for r in build(st)["rows"]}
    assert (rows["n:a"]["style"], rows["n:a"]["style_inferred"]) == ("Pilsner", True)
    assert (rows["n:b"]["style"], "style_inferred" in rows["n:b"]) == (None, False)


def test_a_menu_beer_from_buyam_gets_the_guess_too():
    info = {"name": "Bohemian Pilsner", "url": "https://buy.am/en/restaurants/dargett"}
    st = state({"gargoyle": {"n:a": pair("buyam", last_in_result=True, info=info)}})
    row = build(st)["rows"][0]
    assert (row["badge"], row["style"], row["style_inferred"]) == ("menu", "Pilsner", True)


def test_the_guess_stays_out_of_the_pair_that_the_digest_reads():
    st = state({"beer-city": {"n:a": pair("beercity", in_stock=True, info={"name": "Bever pilsner"})}})
    build(st)
    assert "style" not in st.pairs["beer-city"]["n:a"].info


def test_a_style_the_pair_already_has_is_never_replaced_by_the_guess():
    info = {"name": "Bever pilsner", "style": "Pale Lager"}
    row = build(state({"beer-city": {"n:a": pair("beercity", in_stock=True, info=info)}}))["rows"][0]
    assert (row["style"], "style_inferred" in row) == ("Pale Lager", False)


def test_a_beer_matched_to_untappd_gets_no_guess_even_when_the_match_knows_no_style():
    match = ShopMatchRec(untappd_beer_id=1, url="https://untappd.com/beer/1", via="local")
    info = {"name": "Bever pilsner", "url": "https://untappd.com/beer/1"}
    st = match_state({"beer-city": {"n:x": pair("beercity", in_stock=True, info=info)}}, x=match)
    row = build(st)["rows"][0]
    assert (row["style"], "style_inferred" in row) == (None, False)


def test_a_blocked_match_is_no_match_so_the_guess_applies():
    blocked = ShopMatchRec(via="manual", matched_at=ago(1))
    st = match_state({"beer-city": {"n:x": pair("beercity", in_stock=True, info={"name": "Bever pilsner"})}}, x=blocked)
    assert build(st)["rows"][0]["style"] == "Pilsner"


def test_untappd_check_in_and_hand_entered_beers_get_no_guess():
    untappd = "https://untappd.com/b/pilsner-urquell/1"
    st = state({"gargoyle": {"u:1": pair("untappd_menu", last_in_result=True, info={"name": "Pilsner Urquell", "url": untappd}),
                             "n:m": pair("manual", last_in_result=True,
                                         info={"name": "Pilsner Urquell", "manual_date": "2026-10-10"})},
                "dors": {"u:2": pair("untappd_checkins", info={"name": "Pilsner Urquell", "url": untappd,
                                                               "checkin_at": ago(1)})}})
    rows = build(st)["rows"]
    assert len(rows) == 3
    assert [(r["style"], "style_inferred" in r) for r in rows] == [(None, False)] * 3


def test_row_carries_match_weak_only_when_the_pair_is_flagged():
    """Code review round 3, finding C2: apply_shop_matches (run.py) copies a weak local match's flag
    into the pair's info["match_weak"]; the site row exposes it (False otherwise) so a review page
    can list uncorroborated parenthetical matches first."""
    st = state({"beer-city": {"n:a": pair("beercity", in_stock=True, info={"match_weak": True}),
                              "n:b": pair("beercity", in_stock=True, info={"match_weak": False}),
                              "n:c": pair("beercity", in_stock=True)}})
    weak = {r["beer_key"]: r["match_weak"] for r in build(st)["rows"]}
    assert weak == {"n:a": True, "n:b": False, "n:c": False}


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


# --- v1.2 review page: the shop's own name next to the canonical one, and the matches list ---

def match_state(pairs: dict, **matches: ShopMatchRec) -> State:
    st = state(pairs)
    st.shop_matches = {f"n:{k}": m for k, m in matches.items()}
    return st


CHIMAY = ShopMatchRec(untappd_beer_id=34039, url="https://untappd.com/beer/34039", rating=4.1,
                      logo="https://x/chimay.jpg", name="Chimay Grande Réserve (Blue)",
                      brewery="Bières de Chimay", via="local", weak=True)
CHIMAY_INFO = {"name": "Chimay peres trappistes blue", "brewery": "Chimay",
               "u_name": "Chimay Grande Réserve (Blue)", "u_brewery": "Bières de Chimay",
               "shop_url": "https://beer-city.am/p/chimay", "match_weak": True}


def test_matched_row_carries_the_shops_own_name_brewery_and_how_it_matched():
    st = match_state({"beer-city": {"n:chimay": pair("beercity", in_stock=True, info=CHIMAY_INFO)}}, chimay=CHIMAY)
    row = build(st)["rows"][0]
    assert (row["name"], row["brewery"]) == ("Chimay Grande Réserve (Blue)", "Bières de Chimay")
    assert (row["shop_name"], row["shop_brewery"], row["match_via"]) == (
        "Chimay peres trappistes blue", "Chimay", "local")


def test_unmatched_and_blocked_rows_have_no_shop_identity_fields():
    """A "не то же" block (untappd_beer_id None) is not a match; nor is a pair with no record at all."""
    blocked = ShopMatchRec(via="manual", matched_at=ago(1))
    st = match_state({"beer-city": {"n:a": pair("beercity", in_stock=True), "n:b": pair("beercity", in_stock=True)}},
                     b=blocked)
    for row in build(st)["rows"]:
        assert (row["shop_name"], row["shop_brewery"], row["match_via"]) == (None, None, None)
    assert build(st)["matches"] == []


def test_matches_list_carries_both_sides_of_each_visible_matched_pair():
    st = match_state({"beer-city": {"n:chimay": pair("beercity", in_stock=True, info=CHIMAY_INFO)}}, chimay=CHIMAY)
    assert build(st)["matches"] == [{
        "place_id": "beer-city", "place": "Beer City", "key": "n:chimay",
        "shop_name": "Chimay peres trappistes blue", "shop_brewery": "Chimay",
        "shop_url": "https://beer-city.am/p/chimay", "shop_photo": None,
        "untappd_name": "Chimay Grande Réserve (Blue)", "untappd_brewery": "Bières de Chimay",
        "untappd_url": "https://untappd.com/beer/34039", "untappd_logo": "https://x/chimay.jpg",
        "rating": 4.1, "via": "local", "weak": True,
    }]


def test_matches_list_skips_pairs_that_are_not_visible_on_the_site():
    st = match_state({"beer-city": {"n:chimay": pair("beercity", in_stock=False, info=CHIMAY_INFO)}}, chimay=CHIMAY)
    assert build(st)["matches"] == []


def test_matches_list_has_one_entry_per_place_sharing_a_key():
    st = match_state({"beer-city": {"n:chimay": pair("beercity", in_stock=True, info=CHIMAY_INFO)},
                      "parma": {"n:chimay": pair("parma", in_stock=True, info=CHIMAY_INFO)}}, chimay=CHIMAY)
    assert sorted(m["place_id"] for m in build(st)["matches"]) == ["beer-city", "parma"]


def test_matches_list_is_ordered_weak_then_search_local_manual_then_place_and_name():
    def rec(via, weak=False):
        return ShopMatchRec(untappd_beer_id=1, url="https://untappd.com/beer/1", via=via, weak=weak)
    pairs = {"beer-city": {f"n:{k}": pair("beercity", in_stock=True, info={"name": k})
                           for k in ("manual1", "local1", "search1", "weak1", "search0")},
             "parma": {"n:search0": pair("parma", in_stock=True, info={"name": "search0"})}}
    st = match_state(pairs, manual1=rec("manual"), local1=rec("local"), search1=rec("search"),
                     weak1=rec("local", weak=True), search0=rec("search"))
    order = [(m["place_id"], m["shop_name"]) for m in build(st)["matches"]]
    assert order == [("beer-city", "weak1"), ("beer-city", "search0"), ("beer-city", "search1"),
                     ("parma", "search0"), ("beer-city", "local1"), ("beer-city", "manual1")]


def test_match_shop_photo_is_the_pairs_own_only_when_the_overlay_did_not_replace_it():
    """Yerevan City puts its own photo in info["logo"]; a matched Untappd label overlays it (u_overlay)."""
    own = {"name": "Corona", "logo": "https://yc/photo.jpg"}
    replaced = {"name": "Corona", "logo": "https://x/label.jpg", "u_overlay": {"logo": "https://x/label.jpg"}}
    match = ShopMatchRec(untappd_beer_id=1, url="https://untappd.com/beer/1", via="search")
    st = match_state({"parma": {"n:a": pair("yerevan_city", in_stock=True, info=own),
                                "n:b": pair("yerevan_city", in_stock=True, info=replaced)}}, a=match, b=match)
    photos = {m["key"]: m["shop_photo"] for m in build(st)["matches"]}
    assert photos == {"n:a": "https://yc/photo.jpg", "n:b": None}


def test_row_lists_servings_only_for_a_pair_that_has_several():
    servings = [{"container": "draft", "price_amd": 2800, "volume_ml": None},
                {"container": "bottle", "price_amd": 1500, "volume_ml": 330}]
    st = state({"gargoyle": {
        "u:1": pair("untappd_menu", info={"container": "draft", "price_amd": 2800, "servings": servings}),
        "u:2": pair("untappd_menu", info={"container": "can", "price_amd": 900}),   # a pair saved before servings existed
    }})
    rows = {r["beer_key"]: r for r in build(st)["rows"]}
    assert rows["u:1"]["servings"] == servings
    assert (rows["u:1"]["container"], rows["u:1"]["price_amd"]) == ("draft", 2800)   # the first serving, as before
    assert "servings" not in rows["u:2"]
