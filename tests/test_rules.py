from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from taps.config import Brewery, Config, Place, Settings
from taps.corrections import Corrections, ManualEntry
from taps.model import BreweryBeer, Serving, Sighting, SourceResult
from taps.rules import CHECKIN_EVENT_DAYS, CHECKIN_KEEP_DAYS, MANUAL_EVENT_DAYS, MergeOutcome, merge_results
from taps.sources.manual import manual_result
from taps.state import BeerRec, BreweryNewRec, PairRec, SourceRec, apply_aliases, empty_state
from taps.timeutil import iso, to_yerevan

NOW = datetime(2026, 9, 24, 14, 17, tzinfo=timezone.utc)   # 18:17 in Yerevan
H = timedelta(hours=1)
DAY = timedelta(days=1)


def place(pid, kind, sources, **kw):
    return Place(id=pid, name=pid.title(), kind=kind, sources=sources, **kw)


def venue(venue_id):
    return {"slug": f"venue-{venue_id}", "venue_id": venue_id}


CONFIG = Config(
    places={p.id: p for p in (
        place("gargoyle", "bar", {"untappd_menu": venue(1)}),
        place("dargett-brewpub", "brewpub", {"buyam": {"url": "https://buy.am/en/restaurants/dargett"}},
              brewery_id=265165, brewery_name="Dargett", untappd_venue_id=2),
        place("dors", "brewpub", {"untappd_checkins": venue(3)}, brewery_id=441775, brewery_name="Dors"),
        place("379-torch-brew", "brewpub", {"untappd_checkins": venue(4)}, brewery_id=518994, brewery_name="379"),
        place("tap-station", "bar", {"untappd_checkins": venue(5)}),
        place("izh", "bar", {"untappd_checkins": venue(6)}),
        place("beer-city", "shop", {"beercity": {}}),
        place("parma", "shop", {"parma": {}}),
        place("houl", "shop", {"untappd_checkins": venue(7)}),
        place("alpenberg", "brewpub", {"untappd_checkins": venue(8)}, brewery_name="Alpenberg"),
    )},
    breweries=(Brewery("dors", "Dors", 441775, "dors"),
              Brewery("dargett", "Dargett", 265165, "dargett", list_enabled=True)),
    settings=Settings(),
)


def ready(*keys):
    """State where these sources had a successful run 12 hours ago."""
    state = empty_state(NOW - 30 * DAY)
    for key in keys:
        rec = state.source(key)
        rec.baseline_done, rec.last_ok = True, iso(NOW - 12 * H)
    return state


def merge(state, *results, corrections=Corrections(), now=NOW):
    return merge_results(state, results, CONFIG, corrections, now)


def beer(beer_id, place="gargoyle", **kw):
    """Untappd menu item: key u:<id>, n-key n:zagovor ale <id>."""
    fields = dict(place_id=place, source="untappd_menu", beer_key=f"u:{beer_id}", title=f"Zagovor Ale {beer_id}",
                  name=f"Ale {beer_id}", seen_at=NOW, brewery="Zagovor", untappd_beer_id=beer_id,
                  url=f"https://untappd.com/beer/{beer_id}")
    return Sighting(**{**fields, **kw})


def menu(*sightings, place="gargoyle", **kw):
    return SourceResult(key=f"untappd_menu:{place}", source="untappd_menu", ok=True, sightings=list(sightings),
                        place_id=place, **kw)


def item(item_id, key, place="beer-city", source="beercity", in_stock=True, **kw):
    fields = dict(place_id=place, source=source, beer_key=key, title=f'Beer "{key[2:]}" 0.5 l', name=key[2:],
                  seen_at=NOW, shop_item_id=str(item_id), in_stock=in_stock)
    return Sighting(**{**fields, **kw})


def shop(*sightings, place="beer-city", source="beercity", full=True):
    return SourceResult(key=f"{source}:{place}", source=source, ok=True, sightings=list(sightings),
                        place_id=place, full=full)


def checkin(beer_id, place="tap-station", days=1.0, serving="Draft", **kw):
    fields = dict(place_id=place, source="untappd_checkins", beer_key=f"u:{beer_id}", title=f"Ale {beer_id}",
                  name=f"Ale {beer_id}", seen_at=NOW - days * DAY, brewery="Tap Brewers", untappd_beer_id=beer_id,
                  serving=serving, checkin_id=1000 + beer_id)
    return Sighting(**{**fields, **kw})


def venue_checkins(*sightings, place="tap-station"):
    return SourceResult(key=f"untappd_checkins:{place}", source="untappd_checkins", ok=True,
                        sightings=list(sightings), place_id=place)


def brewery_checkins(*sightings, brewery_id=441775):
    """Brewery page check-ins: like the real source, sightings carry the page's brewery_id."""
    return SourceResult(key=f"untappd_brewery:{brewery_id}", source="untappd_brewery", ok=True,
                        sightings=[replace(s, source="untappd_brewery", brewery_id=brewery_id) for s in sightings],
                        brewery_id=brewery_id)


def entry(place, beer_name, days_ago, by="Аня", brewery="379", untappd_id=None):
    day = to_yerevan(NOW).date() - timedelta(days=days_ago)
    return ManualEntry(f"{place}|{day.isoformat()}|{by}", place, brewery, beer_name, untappd_id, by, day)


def manual(*entries):
    return manual_result(Corrections(sightings=entries), CONFIG, NOW)


def beer_list(*ids, brewery_id=265165):
    beers = [BreweryBeer(brewery_id=brewery_id, untappd_beer_id=i, name=f"Ale {i}", brewery="Dargett",
                         style="IPA - New England", abv=6.5, url=f"https://untappd.com/b/dargett-ale/{i}")
             for i in ids]
    return SourceResult(key=f"untappd_brewery_list:{brewery_id}", source="untappd_brewery_list", ok=True,
                        brewery_id=brewery_id, brewery_beers=beers)


# --- silent first run and events --------------------------------------------

def test_first_run_of_a_source_is_a_silent_baseline():
    state = empty_state(NOW)
    out = merge(state, menu(beer(1), beer(2), beer(3)))
    assert out == MergeOutcome(ok=["untappd_menu:gargoyle"])
    pairs = state.pairs["gargoyle"]
    assert sorted(pairs) == ["u:1", "u:2", "u:3"]
    assert all(p.notified_at == "baseline" and p.event_at is None for p in pairs.values())
    assert state.beers["u:2"] == BeerRec(first_seen_city=iso(NOW), n_key="n:zagovor ale 2")
    assert pairs["u:2"].info == {"source": "untappd_menu", "kind": "menu", "title": "Zagovor Ale 2",
                                 "name": "Ale 2", "brewery": "Zagovor", "url": "https://untappd.com/beer/2"}


def test_second_run_announces_only_the_new_beer():
    state = empty_state(NOW - DAY)
    merge(state, menu(beer(1, price_amd=2300), beer(2)), now=NOW - 12 * H)
    out = merge(state, menu(beer(1, price_amd=2500), beer(2), beer(3)))
    assert out.events == [("gargoyle", "u:3")] and out.ok == ["untappd_menu:gargoyle"]
    new = state.pairs["gargoyle"]["u:3"]
    assert (new.first_seen, new.event_at, new.notified_at, new.star) == (iso(NOW), iso(NOW), None, True)
    old = state.pairs["gargoyle"]["u:1"]
    assert (old.notified_at, old.event_at, old.info["price_amd"]) == ("baseline", None, 2500)   # price: no event
    out = merge(state, menu(beer(1), beer(2), beer(3)), now=NOW + 12 * H)
    assert out.events == []
    assert new.star is True and new.event_at == iso(NOW)   # ⭐ is set once, when the pair is created


def test_star_compares_with_beers_known_before_the_call():
    state = ready("untappd_menu:gargoyle", "beercity:beer-city", "untappd_checkins:tap-station")
    t = iso(NOW - 9 * DAY)
    state.beers.update({
        "u:5": BeerRec(first_seen_city=t, n_key="n:zagovor ale 5"),        # seen at another bar
        "n:konix cassis ruby": BeerRec(first_seen_city=t),                 # seen in a shop
        "u:7": BeerRec(first_seen_city=t, n_key="n:mad hatter porter"),    # seen on a menu
    })
    out = merge(state,
                menu(beer(4), beer(5), beer(6, brewery="Konix Brewery", name="Cassis Ruby")),
                shop(item(1, "n:mad hatter porter"), item(2, "n:brand new stout")),
                venue_checkins(checkin(4)))    # u:4 again: written to beers earlier in this same call
    star = {(p, k): state.pairs[p][k].star for p, k in out.events}
    assert star == {
        ("gargoyle", "u:4"): True,                          # new to the city
        ("gargoyle", "u:5"): False,                         # known u: key
        ("gargoyle", "u:6"): False,                         # its n-key was seen in a shop
        ("beer-city", "n:mad hatter porter"): False,        # the n-key of a known Untappd beer
        ("beer-city", "n:brand new stout"): True,
        ("tap-station", "u:4"): True,                       # beers written in this call do not count
    }


def test_hidden_pair_and_not_craft_item_are_suppressed_hidden_and_not_counted_as_seen():
    state = ready("untappd_menu:gargoyle", "beercity:beer-city")
    corrections = Corrections(hide=frozenset({("gargoyle", "u:9")}), not_craft=("Kilikia",))
    out = merge(state, menu(beer(8), beer(9)),
                shop(item(1, "n:kilikia", title='Beer "Kilikia" 1l', brewery="Kilikia"), item(2, "n:konix bronx")),
                corrections=corrections)
    assert out.events == [("gargoyle", "u:8"), ("beer-city", "n:konix bronx")]
    for pid, key in (("gargoyle", "u:9"), ("beer-city", "n:kilikia")):
        rec = state.pairs[pid][key]
        assert (rec.notified_at, rec.event_at, rec.info["hidden"]) == ("suppressed", None, True)
        assert key not in state.beers
    assert "hidden" not in state.pairs["gargoyle"]["u:8"].info and "u:8" in state.beers


def test_hide_added_later_hides_the_pair_at_once_and_cancels_its_pending_event():
    state = ready("untappd_menu:gargoyle")
    merge(state, menu(beer(1), beer(2), beer(3)), now=NOW - 12 * H)    # three pending events
    corrections = Corrections(hide=frozenset({("gargoyle", "u:1"), ("gargoyle", "n:old name")}),
                              aliases={"n:old name": "u:2"})          # hide written with a key aliased later
    assert merge(state, corrections=corrections) == MergeOutcome()   # Untappd is not fetched in this run
    pairs = state.pairs["gargoyle"]
    for key in ("u:1", "u:2"):
        assert pairs[key].info["hidden"] is True and pairs[key].notified_at == "suppressed"
    assert "hidden" not in pairs["u:3"].info and pairs["u:3"].notified_at is None


def test_shop_filter_added_later_cancels_the_event_and_lifting_it_gives_none():
    state = ready("beercity:beer-city")
    kilikia = item(1, "n:kilikia", title='Beer "Kilikia" 1l', brewery="Kilikia")
    assert merge(state, shop(kilikia), now=NOW - 12 * H).events == [("beer-city", "n:kilikia")]
    rec = state.pairs["beer-city"]["n:kilikia"]
    merge(state, shop(kilikia), corrections=Corrections(not_craft=("Kilikia",)))
    assert rec.notified_at == "suppressed" and rec.info["hidden"] is True
    out = merge(state, shop(kilikia), now=NOW + 12 * H)               # Kilikia removed from not_craft
    assert out.events == [] and rec.notified_at == "suppressed" and "hidden" not in rec.info


def test_hide_lifted_later_gives_no_event_and_stays_suppressed():
    state = ready("untappd_menu:gargoyle")
    hidden = Corrections(hide=frozenset({("gargoyle", "u:1")}))
    assert merge(state, menu(beer(1), beer(2)), corrections=hidden, now=NOW - 12 * H).events == [("gargoyle", "u:2")]
    rec = state.pairs["gargoyle"]["u:1"]
    assert rec.notified_at == "suppressed" and rec.info["hidden"] is True
    out = merge(state, menu(beer(1), beer(2)))                      # hide removed from corrections.yaml
    assert out.events == [] and rec.notified_at == "suppressed" and "hidden" not in rec.info


def test_shop_item_hidden_by_brand_stays_hidden_when_a_later_sighting_lacks_brand():
    state = ready("beercity:beer-city")
    corrections = Corrections(not_craft=("Efes",))
    key = "n:lager"
    out1 = merge(state, shop(item(1, key, brewery="Efes")), corrections=corrections)
    rec = state.pairs["beer-city"][key]
    assert out1.events == [] and (rec.notified_at, rec.info["hidden"]) == ("suppressed", True)
    out2 = merge(state, shop(item(1, key, brewery=None)), corrections=corrections, now=NOW + 12 * H)
    assert out2.events == []
    assert rec.notified_at == "suppressed" and rec.info["hidden"] is True


def test_matched_shop_item_stays_visible_after_partial_refresh_even_with_a_not_craft_untappd_brewery():
    """Code review (HIGH): run.py's apply_shop_matches must never let a match's canonical Untappd
    brewery leak into rec.info["brewery"] -- the brand fallback above (a partial refresh that lacks
    brand falls back to rec.info["brewery"]) must keep seeing the shop's own brand, so a beer whose
    matched Untappd brewery name happens to contain a not_craft word is not wrongly hidden."""
    from taps.run import apply_shop_matches
    from taps.state import ShopMatchRec

    state = ready("beercity:beer-city")
    corrections = Corrections(not_craft=("Efes",))
    key = "n:craft lager"
    title = 'Beer "Craft Lager" 0.5 l'
    out1 = merge(state, shop(item(1, key, brewery="Craftbrew", title=title)), corrections=corrections)
    assert out1.events == [("beer-city", key)]   # a genuine craft brand: not hidden

    state.shop_matches[key] = ShopMatchRec(untappd_beer_id=1, name="Craft Lager", brewery="Efes International",
                                           matched_at=iso(NOW), via="local")
    apply_shop_matches(state)

    merge(state, shop(item(1, key, brewery=None, title=title)), corrections=corrections, now=NOW + 12 * H)
    rec = state.pairs["beer-city"][key]
    assert "hidden" not in rec.info and rec.notified_at != "suppressed"


# --- shops: stock, item keys, aliases, Parma colours -------------------------

def test_shop_country_and_photo_reach_the_pair_and_country_outlives_a_sighting_without_it():
    """The product page (country) is read once, for a new item; the listing photo comes with every sighting."""
    state = ready("beercity:beer-city")
    key = "n:hard root ipa"
    first, later = "https://www.beer-city.am/media/product-img/small_1.jpg", "https://www.beer-city.am/media/product-img/small_2.jpg"
    merge(state, shop(item(1, key, country="Russia", logo=first)))
    info = state.pairs["beer-city"][key].info
    assert (info["country"], info["logo"]) == ("Russia", first)
    merge(state, shop(item(1, key, logo=later)), now=NOW + 12 * H)
    assert (info["country"], info["logo"]) == ("Russia", later)


def test_out_of_stock_new_item_becomes_an_event_only_when_in_stock():
    state = ready("beercity:beer-city")
    key = "n:hard root ipa"
    out = merge(state, shop(item(1, key, in_stock=False)))
    rec = state.pairs["beer-city"][key]
    assert out.events == [] and (rec.notified_at, rec.event_at, rec.in_stock) == (None, None, False)
    assert key in state.beers                    # an out-of-stock item still counts as seen in the city
    assert merge(state, shop(item(1, key, in_stock=False)), now=NOW + 12 * H).events == []
    assert rec.event_at is None
    out = merge(state, shop(item(1, key)), now=NOW + 24 * H)
    assert out.events == [("beer-city", key)]
    assert (rec.event_at, rec.notified_at, rec.in_stock, rec.star) == (iso(NOW + 24 * H), None, True, True)
    merge(state, shop(item(1, key, in_stock=False)), now=NOW + 36 * H)
    assert merge(state, shop(item(1, key)), now=NOW + 48 * H).events == []   # back in stock: not an event
    assert rec.event_at == iso(NOW + 24 * H)


def test_out_of_stock_items_seen_in_the_baseline_run_never_become_events():
    state = empty_state(NOW)
    merge(state, shop(item(1, "n:gose", in_stock=False), item(2, "n:pale")))
    assert state.pairs["beer-city"]["n:gose"].notified_at == "baseline"
    out = merge(state, shop(item(1, "n:gose"), item(2, "n:pale")), now=NOW + 12 * H)
    assert out.events == [] and state.pairs["beer-city"]["n:gose"].event_at is None


def test_never_in_stock_item_seen_in_a_silent_run_is_baseline():
    state = ready("beercity:beer-city")
    merge(state, shop(item(1, "n:gose", in_stock=False)))
    rec = state.pairs["beer-city"]["n:gose"]
    out = merge(state, shop(item(1, "n:gose")), now=NOW + 4 * DAY)    # last run 4 days ago: stale baseline
    assert out.events == [] and (rec.notified_at, rec.event_at) == ("baseline", None)
    assert merge(state, shop(item(1, "n:gose")), now=NOW + 4 * DAY + 12 * H).events == []


@pytest.mark.parametrize("stock", [(True, False), (False, True)])
def test_one_key_sold_as_two_items_is_in_stock_if_either_is(stock):
    state = ready("beercity:beer-city")
    small = item(3, "n:mythos", title='Beer "Mythos" 0.33 l', in_stock=stock[0])
    big = item(5, "n:mythos", title='Beer "Mythos" 0.5 l', in_stock=stock[1])
    out = merge(state, shop(small, big))
    rec = state.pairs["beer-city"]["n:mythos"]
    assert out.events == [("beer-city", "n:mythos")] and rec.in_stock is True and rec.event_at == iso(NOW)
    merge(state, shop(replace(small, in_stock=False), replace(big, in_stock=False)), now=NOW + 12 * H)
    assert rec.in_stock is False


def test_shop_item_keeps_its_first_key_when_the_title_changes():
    state = ready("beercity:beer-city")
    merge(state, shop(item(555, "n:konix bronx", title='Beer "Konix" Bronx 0.33 l')))
    renamed = item(555, "n:konix bronx neipa", title='Beer "Konix" Bronx NEIPA 0.33 l')
    out = merge(state, shop(renamed), now=NOW + 12 * H)
    assert out.events == []
    assert list(state.pairs["beer-city"]) == ["n:konix bronx"]
    assert state.shop_items["beer-city"] == {"555": "n:konix bronx"}
    assert state.pairs["beer-city"]["n:konix bronx"].info["title"] == 'Beer "Konix" Bronx NEIPA 0.33 l'


def test_aliased_shop_item_is_not_announced_again_in_three_runs():
    state = ready("beercity:beer-city")
    bronx = item(555, "n:konix bronx", title='Beer "Konix" Bronx 0.33 l')
    assert merge(state, shop(bronx), now=NOW - 12 * H).events == [("beer-city", "n:konix bronx")]
    state.pairs["beer-city"]["n:konix bronx"].notified_at = iso(NOW - 11 * H)   # sent in a digest
    aliases = {"n:konix bronx": "u:3539672"}                                     # Oleg glued it to Untappd
    twin = item(556, "n:konix bronx", title='Beer "Konix" Bronx 0.5 l')         # a new item id, same beer
    for run in range(3):
        apply_aliases(state, aliases)            # run.py does this right after loading state
        out = merge(state, shop(bronx, twin), corrections=Corrections(aliases=aliases), now=NOW + run * 12 * H)
        assert out.events == []
    assert list(state.pairs["beer-city"]) == ["u:3539672"]
    assert state.shop_items["beer-city"] == {"555": "u:3539672", "556": "u:3539672"}


def test_parma_code_without_colour_joins_the_existing_key_with_colour():
    state = ready("parma:parma", "beercity:beer-city")
    t = iso(NOW - 5 * DAY)
    for pid, source in (("parma", "parma"), ("beer-city", "beercity")):
        state.pairs[pid] = {"n:dahook light": PairRec(first_seen=t, last_seen=t, notified_at=t,
                                                      info={"source": source, "kind": "shop"})}
        state.shop_items[pid] = {"111": "n:dahook light"}
    out = merge(state,
                shop(item(222, "n:dahook", place="parma", source="parma"), place="parma", source="parma"),
                shop(item(222, "n:dahook")))
    assert out.events == [("beer-city", "n:dahook")]     # only Parma joins colours
    assert list(state.pairs["parma"]) == ["n:dahook light"]
    assert state.shop_items["parma"] == {"111": "n:dahook light", "222": "n:dahook light"}


def test_display_fields_survive_sightings_that_lack_them():
    state = ready("beercity:beer-city")
    key = "n:hard root ipa"
    merge(state, shop(item(1, key, brewery="Hard Root", abv=6.5, volume_ml=450, container="can", price_amd=1900)))
    merge(state, shop(item(1, key, price_amd=1700)), now=NOW + 12 * H)   # known item: product page not read
    assert state.pairs["beer-city"][key].info == {
        "source": "beercity", "kind": "shop", "title": 'Beer "hard root ipa" 0.5 l', "name": "hard root ipa",
        "brewery": "Hard Root", "abv": 6.5, "volume_ml": 450, "container": "can", "price_amd": 1700,
        "shop_item_id": "1",
    }


# --- menus and places --------------------------------------------------------

def test_items_of_a_new_menu_tab_are_silent():
    state = ready("untappd_menu:gargoyle")
    state.sources["untappd_menu:gargoyle"].seen_menu_ids = ["100"]
    tabs = [beer(1, menu_id="100"), beer(2, menu_id="200"), beer(3, menu_id="200")]
    out = merge(state, menu(*tabs))
    assert out.events == [("gargoyle", "u:1")]
    assert state.pairs["gargoyle"]["u:2"].notified_at == "baseline"
    assert state.sources["untappd_menu:gargoyle"].seen_menu_ids == ["100", "200"]
    out = merge(state, menu(*tabs, beer(4, menu_id="200")), now=NOW + 12 * H)
    assert out.events == [("gargoyle", "u:4")]           # the tab is known now


def test_a_new_place_is_silent_until_its_own_source_has_run():
    state = ready("untappd_brewery:441775", "untappd_checkins:tap-station")   # izh was just added
    out = merge(state, brewery_checkins(checkin(1, "tap-station"), checkin(2, "izh")))
    assert out.events == [("tap-station", "u:1")]
    assert state.pairs["izh"]["u:2"].notified_at == "baseline" and "u:2" in state.beers
    out = merge(state, venue_checkins(checkin(3, "izh"), place="izh"), now=NOW + 12 * H)   # its first run
    assert out.events == [] and state.pairs["izh"]["u:3"].notified_at == "baseline"
    out = merge(state, brewery_checkins(checkin(4, "izh")), now=NOW + 24 * H)
    assert out.events == [("izh", "u:4")]


def test_same_beer_under_another_key_at_the_place_is_not_an_event():
    state = ready("untappd_menu:gargoyle", "untappd_checkins:tap-station")
    t = iso(NOW - 5 * DAY)
    state.pairs["gargoyle"] = {"u:100": PairRec(first_seen=t, last_seen=t, notified_at=t,
                                                info={"source": "untappd_menu", "kind": "menu"})}
    state.beers["u:100"] = BeerRec(first_seen_city=t, n_key="n:zagovor black sails")
    state.pairs["tap-station"] = {"n:379 hazy pale": PairRec(first_seen=t, last_seen=t, notified_at=t,
                                                             info={"source": "manual", "kind": "manual"})}
    out = merge(state,
                menu(beer(100, name="Black Sails"), beer(200, name="Black Sails", brewery="Zagovor Brewery"),
                     beer(201)),
                venue_checkins(checkin(300, name="Hazy Pale", brewery="379")))
    assert out.events == [("gargoyle", "u:201")]
    assert state.pairs["gargoyle"]["u:200"].notified_at == "suppressed"      # Untappd changed the id
    assert state.pairs["tap-station"]["u:300"].notified_at == "suppressed"   # a friend reported it by name


def test_menu_row_stays_a_menu_row_when_a_friend_reports_the_same_beer():
    state = ready("untappd_menu:gargoyle", "untappd_checkins:tap-station")
    merge(state, menu(beer(1)), manual(entry("tap-station", None, 1, brewery=None, untappd_id=5)), now=NOW - 12 * H)
    out = merge(state, menu(beer(1)), manual(entry("gargoyle", None, 1, brewery=None, untappd_id=1)),
                venue_checkins(checkin(5)))
    assert out.events == []
    on_menu = state.pairs["gargoyle"]["u:1"].info
    assert on_menu["source"] == "untappd_menu" and "manual_id" not in on_menu
    seen = state.pairs["tap-station"]["u:5"].info        # a check-in outranks a friend's report
    assert seen["kind"] == "checkin" and seen["checkin_at"] == iso(NOW - DAY)


# --- check-ins ---------------------------------------------------------------

IGNORED_CHECKINS = {
    "place-with-menu": brewery_checkins(checkin(1, "gargoyle")),
    "brewpub-with-buyam-menu": brewery_checkins(checkin(1, "dargett-brewpub", serving=None), brewery_id=265165),
    "untappd-at-home": venue_checkins(checkin(1, at_home=True)),
    "25-days-old": venue_checkins(checkin(1, days=25)),
}


@pytest.mark.parametrize("result", IGNORED_CHECKINS.values(), ids=IGNORED_CHECKINS.keys())
def test_ignored_checkins_leave_no_trace(result):
    state = ready(result.key, "untappd_checkins:dors", "untappd_checkins:tap-station")
    out = merge(state, result)
    assert out == MergeOutcome(ok=[result.key])
    assert state.pairs == {} and state.beers == {}


@pytest.mark.parametrize("serving", ["Bottle", "Can", None, "Draft"])
def test_checkins_count_regardless_of_serving_at_a_bar(serving):
    """v1.1 owner decision: bars no longer require Draft or an empty serving."""
    state = ready("untappd_checkins:tap-station")
    out = merge(state, venue_checkins(checkin(1, serving=serving), place="tap-station"))
    assert out.events == [("tap-station", "u:1")]


@pytest.mark.parametrize("serving", ["Bottle", "Can", None, "Draft"])
def test_checkins_count_regardless_of_serving_or_brewery_at_a_brewpub(serving):
    """v1.1 owner decision: a brewpub counts any check-in, even another brewery's, regardless of serving."""
    state = ready("untappd_checkins:dors")
    out = merge(state, venue_checkins(checkin(1, "dors", serving=serving, brewery="Pulpulak Brewery"),
                                      place="dors"))
    assert out.events == [("dors", "u:1")]


@pytest.mark.parametrize("serving", ["Bottle", "Can", None, "Draft"])
def test_shop_checkins_count_regardless_of_serving(serving):
    """v1.1: a shop (e.g. Houl) counts a bottle/can/unlabelled check-in too."""
    state = ready("untappd_checkins:houl")
    out = merge(state, venue_checkins(checkin(1, "houl", serving=serving), place="houl"))
    assert out.events == [("houl", "u:1")]


def test_checkin_age_limits():
    assert (CHECKIN_EVENT_DAYS, CHECKIN_KEEP_DAYS, MANUAL_EVENT_DAYS) == (7, 21, 3)
    state = ready("untappd_checkins:tap-station")
    out = merge(state, venue_checkins(checkin(1, days=7), checkin(2, days=10), checkin(3, days=21),
                                      checkin(4, days=21.01)))
    assert out.events == [("tap-station", "u:1")]
    pairs = state.pairs["tap-station"]
    assert (pairs["u:2"].notified_at, pairs["u:2"].event_at) == ("suppressed", None)
    assert pairs["u:3"].notified_at == "suppressed"
    assert pairs["u:2"].info["checkin_at"] == iso(NOW - 10 * DAY)
    assert "u:4" not in pairs and "u:4" not in state.beers
    assert {"u:1", "u:2", "u:3"} <= set(state.beers)


def test_checkin_time_is_the_latest_checkin_of_the_beer_at_the_place():
    state = ready("untappd_brewery:441775", "untappd_checkins:tap-station")
    out = merge(state, brewery_checkins(checkin(1, days=1)), venue_checkins(checkin(1, days=4)))
    assert out.events == [("tap-station", "u:1")]
    assert state.pairs["tap-station"]["u:1"].info["checkin_at"] == iso(NOW - DAY)


def test_checkin_backfills_style_and_abv_from_a_menu_pair_with_the_same_key():
    state = ready("untappd_menu:gargoyle", "untappd_brewery:441775", "untappd_checkins:dors")
    merge(state, menu(beer(5, style="Porter", abv=5.5)), now=NOW - 12 * H)
    out = merge(state, brewery_checkins(checkin(5, "dors", serving=None, brewery=None)))
    assert out.events == [("dors", "u:5")]
    info = state.pairs["dors"]["u:5"].info
    assert info["style"] == "Porter" and info["abv"] == 5.5


def test_checkin_backfills_from_the_beers_cache_when_no_menu_pair_exists():
    """v1.1 §2: a beer seen only in check-ins borrows rating/style/abv/ibu from state.beers, filled by
    run.fetch_beer_ratings from the beer's own Untappd page -- no menu pair to borrow from here."""
    state = ready("untappd_checkins:tap-station")
    state.beers["u:9"] = BeerRec(first_seen_city=iso(NOW - DAY), rating=3.82, style="Fruit Beer", abv=6.2, ibu=18,
                                 rating_at=iso(NOW))
    out = merge(state, venue_checkins(checkin(9)))
    assert out.events == [("tap-station", "u:9")]
    info = state.pairs["tap-station"]["u:9"].info
    assert (info["rating"], info["style"], info["abv"], info["ibu"]) == (3.82, "Fruit Beer", 6.2, 18)


def test_checkin_backfill_prefers_a_menu_pair_over_the_cache():
    state = ready("untappd_menu:gargoyle", "untappd_brewery:441775", "untappd_checkins:dors")
    merge(state, menu(beer(5, style="Porter", abv=5.5)), now=NOW - 12 * H)
    state.beers["u:5"].rating, state.beers["u:5"].style = 4.5, "Stale Cache Style"
    out = merge(state, brewery_checkins(checkin(5, "dors", serving=None, brewery=None)))
    assert out.events == [("dors", "u:5")]
    assert state.pairs["dors"]["u:5"].info["style"] == "Porter"   # the menu pair wins, not the cache


def test_checkin_backfill_from_the_cache_does_not_overwrite_known_fields():
    state = ready("untappd_checkins:tap-station")
    state.beers["u:9"] = BeerRec(first_seen_city=iso(NOW - DAY), rating=3.82, style="Fruit Beer")
    merge(state, venue_checkins(checkin(9, style="Sour")))
    assert state.pairs["tap-station"]["u:9"].info["style"] == "Sour"   # the sighting's own value wins


def test_merged_multi_venue_place_first_run_after_state_merge_is_silent():
    """v1.1: beer-academy-ethnograph's pairs were folded into beer-academy (state.merge_places). A run
    that now fetches both venues as one merged result must not re-flood events for beers already known
    there under the old place id -- the source key ("untappd_checkins:beer-academy") already had its
    baseline before the merge, so the place is not "new"."""
    ba = place("beer-academy", "brewpub", {"untappd_checkins": {
        "venues": [{"slug": "beer-academy", "venue_id": 100}, {"slug": "beer-academy-ethnograph", "venue_id": 200}],
    }}, brewery_id=143586, brewery_name="Beer Academy")
    config = Config(places={"beer-academy": ba}, breweries=(), settings=Settings())
    state = ready("untappd_checkins:beer-academy")
    state.pairs["beer-academy"] = {"u:1": PairRec(first_seen=iso(NOW - 9 * DAY), last_seen=iso(NOW - 9 * DAY),
                                                  notified_at="baseline")}
    state.beers["u:1"] = BeerRec(first_seen_city=iso(NOW - 9 * DAY))
    result = SourceResult(key="untappd_checkins:beer-academy", source="untappd_checkins", ok=True,
                          place_id="beer-academy", sightings=[checkin(1, "beer-academy", serving=None)])
    out = merge_results(state, [result], config, Corrections(), NOW)
    assert out.events == []
    assert state.pairs["beer-academy"]["u:1"].notified_at == "baseline"


# --- manual ------------------------------------------------------------------

def test_manual_entry_is_an_event_within_three_days_and_once_per_entry_id():
    # started well before the entries' dates: every place is new (no source has run yet), but this is not
    # a state-loss situation, so recent manual entries still give events.
    state = empty_state(NOW - 10 * DAY)
    state.announced_manual = [entry("izh", "Hazy Pale", 1, by="Олег").id]
    out = merge(state, manual(entry("tap-station", "Hazy Pale", 2),
                              entry("tap-station", "Gose", 3, by="Ваня"),
                              entry("tap-station", "Old Stout", 5, by="Лёша"),
                              entry("izh", "Hazy Pale Ale", 1, by="Олег")))   # renamed after it was announced
    assert out.events == [("tap-station", "n:379 hazy pale"), ("tap-station", "n:379 gose")]
    rec = state.pairs["tap-station"]["n:379 hazy pale"]
    assert (rec.event_at, rec.notified_at) == (iso(NOW), None)
    assert rec.info == {"source": "manual", "kind": "manual", "title": "379 Hazy Pale", "name": "Hazy Pale",
                        "brewery": "379", "manual_id": "tap-station|2026-09-22|Аня", "manual_by": "Аня",
                        "manual_date": "2026-09-22"}
    assert state.pairs["tap-station"]["n:379 old stout"].notified_at == "suppressed"
    assert state.pairs["izh"]["n:379 hazy pale ale"].notified_at == "suppressed"
    assert {"n:379 hazy pale", "n:379 old stout", "n:379 hazy pale ale"} <= set(state.beers)


def test_manual_entry_dated_before_state_started_is_suppressed_after_state_loss():
    state = empty_state(NOW - H)             # state.json was just (re)created, an hour ago
    out = merge(state, manual(entry("tap-station", "Hazy Pale", 2)))    # dated two days ago: before started_at
    assert out.events == []
    rec = state.pairs["tap-station"]["n:379 hazy pale"]
    assert (rec.notified_at, rec.event_at) == ("suppressed", None)


def test_manual_pair_no_longer_in_corrections_becomes_suppressed_and_out_of_result():
    state = empty_state(NOW - 10 * DAY)
    merge(state, manual(entry("tap-station", "Hazy Pale", 2)), now=NOW - 12 * H)
    rec = state.pairs["tap-station"]["n:379 hazy pale"]
    assert rec.last_in_result is True and rec.event_at is not None and rec.notified_at is None
    out = merge(state, manual())             # the entry was withdrawn from corrections.yaml
    assert out.events == []
    assert rec.last_in_result is False
    assert rec.notified_at == "suppressed"


def test_manual_reconciliation_does_not_touch_pairs_taken_over_by_a_later_source_in_the_same_run():
    state = ready("untappd_menu:gargoyle", "untappd_checkins:tap-station")
    merge(state, menu(beer(1)), manual(entry("tap-station", None, 1, brewery=None, untappd_id=5)), now=NOW - 12 * H)
    merge(state, menu(beer(1)), manual(entry("gargoyle", None, 1, brewery=None, untappd_id=1)),
          venue_checkins(checkin(5)))
    # the check-in (processed after the manual result in the same run) took ownership of u:5
    seen = state.pairs["tap-station"]["u:5"]
    assert seen.info["source"] == "untappd_checkins" and seen.last_in_result is True and seen.notified_at is None


# --- breaker verdicts ----------------------------------------------------------

def test_failed_result_changes_only_the_failure_fields_of_its_source():
    state = ready("untappd_menu:gargoyle")
    merge(state, menu(beer(1), beer(2)), now=NOW - 12 * H)
    before = state.to_dict()
    failed = SourceResult(key="untappd_menu:gargoyle", source="untappd_menu", ok=False, error="cloudflare",
                          place_id="gargoyle")
    assert merge(state, failed) == MergeOutcome(failed=[("untappd_menu:gargoyle", "cloudflare")])
    after = state.to_dict()
    rec_before = before["sources"].pop("untappd_menu:gargoyle")
    rec_after = after["sources"].pop("untappd_menu:gargoyle")
    assert after == before
    assert rec_after == {**rec_before, "fail_streak": 1, "last_error": "cloudflare"}


def test_tripped_results_change_no_pairs_until_accepted_as_a_silent_baseline():
    key = "untappd_menu:gargoyle"
    state = ready(key)
    merge(state, menu(*[beer(i) for i in range(1, 31)]), now=NOW - 12 * H)
    pairs_before = state.to_dict()["pairs"]
    rebuilt = menu(*[beer(i) for i in range(1, 11)], beer(99))    # 11 of 30: fewer than half
    for run in range(2):
        assert merge(state, rebuilt, now=NOW + run * 12 * H) == MergeOutcome(tripped=[(key, "shrink")])
        assert state.to_dict()["pairs"] == pairs_before and "u:99" not in state.beers
    assert state.sources[key].trip_streak == 2
    out = merge(state, rebuilt, now=NOW + 24 * H)                 # third similar result: a real new menu
    assert out == MergeOutcome(accepted=[key], ok=[key])
    pairs = state.pairs["gargoyle"]
    assert (pairs["u:99"].notified_at, pairs["u:99"].event_at) == ("baseline", None)
    assert pairs["u:5"].last_in_result is True and pairs["u:20"].last_in_result is False


# --- last_in_result and the source record ----------------------------------------

def test_last_in_result_follows_the_latest_full_result_of_the_same_source():
    state = ready("untappd_menu:gargoyle", "beercity:beer-city")
    merge(state, menu(beer(1), beer(2)), shop(item(1, "n:gose"), item(2, "n:pale")),
          manual(entry("gargoyle", "Gose", 1, brewery="Dargett")), now=NOW - 12 * H)
    merge(state, menu(beer(1)))
    g = state.pairs["gargoyle"]
    assert (g["u:1"].last_in_result, g["u:2"].last_in_result, g["n:dargett gose"].last_in_result) == (True, False, True)
    merge(state, shop(item(3, "n:stout"), full=False), now=NOW + 12 * H)      # partial Beer City run
    b = state.pairs["beer-city"]
    assert all(b[k].last_in_result for k in ("n:gose", "n:pale", "n:stout"))
    merge(state, shop(item(1, "n:gose"), item(3, "n:stout")), now=NOW + 24 * H)   # full walk
    assert [k for k, r in b.items() if not r.last_in_result] == ["n:pale"]
    merge(state, shop(item(2, "n:pale"), full=False), now=NOW + 36 * H)       # back in a partial run
    assert b["n:pale"].last_in_result is True


def test_partial_beercity_run_never_touches_already_known_items():
    state = ready("beercity:beer-city")
    merge(state, shop(item(1, "n:gose", price_amd=1500, in_stock=True)), now=NOW - 12 * H)
    rec = state.pairs["beer-city"]["n:gose"]
    out = merge(state, shop(item(1, "n:gose", price_amd=1900, in_stock=False),
                            item(2, "n:new-arrival"), full=False), now=NOW)
    assert rec.info["price_amd"] == 1500 and rec.in_stock is True
    assert "n:new-arrival" in state.pairs["beer-city"]
    assert state.shop_items["beer-city"] == {"1": "n:gose", "2": "n:new-arrival"}
    assert out.events == [("beer-city", "n:new-arrival")]


def test_country_backfill_fills_a_known_item_even_in_a_partial_run_without_touching_first_seen_or_events():
    state = ready("beercity:beer-city")
    merge(state, shop(item(1, "n:gose", brewery="Konix", abv=5.0)), now=NOW - 12 * H)
    rec = state.pairs["beer-city"]["n:gose"]
    first_seen, notified = rec.first_seen, rec.notified_at
    out = merge(state, shop(item(1, "n:gose", brewery="Konix", abv=5.5, country="Russia", country_checked=True),
                            full=False), now=NOW)
    assert (rec.info["country"], rec.info["country_checked"], rec.info["abv"]) == ("Russia", True, 5.5)
    assert (rec.first_seen, rec.notified_at, out.events) == (first_seen, notified, [])
    assert list(state.pairs["beer-city"]) == ["n:gose"] and state.shop_items["beer-city"] == {"1": "n:gose"}


def test_country_checked_without_a_country_is_remembered_and_never_blanks_a_known_country():
    state = ready("parma:parma")
    merge(state, shop(item(1, "n:gose", place="parma", source="parma", country="Armenia")), now=NOW - 12 * H)
    merge(state, shop(item(1, "n:gose", place="parma", source="parma", country_checked=True), place="parma",
                      source="parma"), now=NOW)
    info = state.pairs["parma"]["n:gose"].info
    assert (info["country"], info["country_checked"]) == ("Armenia", True)
    merge(state, shop(item(1, "n:gose", place="parma", source="parma"), place="parma", source="parma"),
          now=NOW + 12 * H)   # an ordinary later sighting keeps both
    info = state.pairs["parma"]["n:gose"].info
    assert (info["country"], info["country_checked"]) == ("Armenia", True)


def test_source_record_after_a_full_and_a_partial_merge():
    state = empty_state(NOW)
    rec = state.source("untappd_menu:gargoyle")
    rec.fail_streak, rec.last_error = 2, "network"
    updated = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)
    merge(state, menu(beer(1, menu_id="100"), beer(2, menu_id="200"), beer(3, menu_id="100"),
                      menu_updated_at=updated))
    assert rec == SourceRec(baseline_done=True, last_ok=iso(NOW), last_full=iso(NOW), last_count=3,
                            seen_menu_ids=["100", "200"], menu_updated_at=iso(updated))
    bc = state.source("beercity:beer-city")
    bc.baseline_done, bc.last_ok, bc.last_full, bc.last_count = True, iso(NOW - 6 * H), iso(NOW - 6 * H), 250
    merge(state, shop(item(1, "n:gose"), full=False))
    assert (bc.last_ok, bc.last_full, bc.last_count) == (iso(NOW), iso(NOW - 6 * H), 250)


# --- brewery beer list (🏭) ----------------------------------------------------------

def test_brewery_list_baseline_then_new_ids_above_the_maximum():
    state = empty_state(NOW - 7 * DAY)
    out = merge(state, beer_list(100, 200, 300), now=NOW - 6 * DAY)
    rec = state.sources["untappd_brewery_list:265165"]
    assert out == MergeOutcome(ok=["untappd_brewery_list:265165"])
    assert (rec.max_beer_id, state.brewery_new, state.pairs) == (300, {}, {})
    assert state.beers["u:300"] == BeerRec(first_seen_city=iso(NOW - 6 * DAY), n_key="n:dargett ale 300")
    state.beers["u:302"] = BeerRec(first_seen_city=iso(NOW - DAY), n_key="n:dargett ale 302")   # on a menu
    out = merge(state, beer_list(100, 200, 250, 300, 301, 302))       # six days later
    assert out.brewery_events == ["u:301", "u:302"] and out.events == []
    assert state.brewery_new["u:301"] == BreweryNewRec(
        brewery_id=265165, found_at=iso(NOW), star=True, notified_at=None,
        info={"name": "Ale 301", "brewery": "Dargett", "style": "IPA - New England", "abv": 6.5,
              "url": "https://untappd.com/b/dargett-ale/301"})
    assert state.brewery_new["u:302"].star is False
    assert "u:250" in state.beers and "u:250" not in state.brewery_new    # a lower unknown id is silent
    assert rec.max_beer_id == 302 and state.pairs == {}


# --- servings: several ways one beer is poured at one place ---------------------------------------------

TAP, BOTTLE = Serving("draft", 1800, 500), Serving("bottle", 1200, 330)
TAP_INFO = {"container": "draft", "price_amd": 1800, "volume_ml": 500}
BOTTLE_INFO = {"container": "bottle", "price_amd": 1200, "volume_ml": 330}


def two_servings(beer_id=1, **kw):
    return beer(beer_id, container="draft", price_amd=1800, volume_ml=500, servings=(TAP, BOTTLE), **kw)


def test_servings_are_stored_as_a_list_next_to_the_first_serving_and_give_one_event():
    state = ready("untappd_menu:gargoyle")
    out = merge(state, menu(two_servings()))
    info = state.pairs["gargoyle"]["u:1"].info
    assert out.events == [("gargoyle", "u:1")]
    assert info.get("servings") == [TAP_INFO, BOTTLE_INFO]
    assert (info["container"], info["price_amd"], info["volume_ml"]) == ("draft", 1800, 500)


def test_a_new_serving_of_an_announced_beer_is_not_an_event():
    state = ready("untappd_menu:gargoyle")
    merge(state, menu(beer(1, container="draft", price_amd=1800, volume_ml=500)), now=NOW - 12 * H)
    rec = state.pairs["gargoyle"]["u:1"]
    rec.notified_at = iso(NOW - 6 * H)   # the digest already announced the beer
    before = (rec.first_seen, rec.event_at, rec.notified_at, rec.star)
    out = merge(state, menu(two_servings()))     # the bar now also sells it in bottles
    assert out == MergeOutcome(ok=["untappd_menu:gargoyle"])
    assert (rec.first_seen, rec.event_at, rec.notified_at, rec.star) == before
    assert rec.info["servings"] == [TAP_INFO, BOTTLE_INFO]


def test_a_serving_that_left_the_menu_is_removed_from_the_pair():
    state = ready("untappd_menu:gargoyle")
    merge(state, menu(two_servings()), now=NOW - 12 * H)
    merge(state, menu(beer(1, container="draft", price_amd=1800, volume_ml=500)))   # the bottle is gone
    info = state.pairs["gargoyle"]["u:1"].info
    assert "servings" not in info
    assert (info["container"], info["price_amd"], info["volume_ml"]) == ("draft", 1800, 500)


def test_two_manual_entries_for_one_beer_are_one_event_with_both_servings():
    state = empty_state(NOW - 10 * DAY)
    tap = replace(entry("tap-station", "Hazy Pale", 1), container="draft", price_amd=2800)
    bottle = replace(tap, container="bottle", price_amd=None)
    out = merge(state, manual(tap, bottle))
    assert out.events == [("tap-station", "n:379 hazy pale")]
    info = state.pairs["tap-station"]["n:379 hazy pale"].info
    assert info["servings"] == [{"container": "draft", "price_amd": 2800, "volume_ml": None},
                                {"container": "bottle", "price_amd": None, "volume_ml": None}]
    assert (info["container"], info["price_amd"], info["manual_id"]) == ("draft", 2800, tap.id)


@pytest.mark.parametrize("event_at, notified_at", [
    (iso(NOW - 12 * H), iso(NOW - 6 * H)),   # announced in the digest
    (None, "suppressed"),                    # recorded silently, like the Ferment board's Untappd-keyed pairs
])
def test_a_manual_entry_added_to_a_known_beer_adds_a_serving_without_a_new_event(event_at, notified_at):
    state = empty_state(NOW - 10 * DAY)
    tap = replace(entry("tap-station", "Hazy Pale", 1), container="draft", price_amd=2800)
    merge(state, manual(tap), now=NOW - 12 * H)
    rec = state.pairs["tap-station"]["n:379 hazy pale"]
    rec.event_at, rec.notified_at, state.announced_manual = event_at, notified_at, [tap.id]
    out = merge(state, manual(tap, replace(tap, container="bottle", price_amd=None)))
    assert out.events == []
    assert (rec.event_at, rec.notified_at, state.announced_manual) == (event_at, notified_at, [tap.id])
    assert [s["container"] for s in rec.info["servings"]] == ["draft", "bottle"]
    assert merge(state, manual(tap)).events == [] and "servings" not in rec.info   # and back when withdrawn


# --- a check-in takes a hand-entered pair over but carries no serving data ------------------------------

def board(beer_id=5, place="tap-station"):
    """A hand-entered board: one Untappd beer on tap and in bottles."""
    tap = replace(entry(place, None, 1, brewery=None, untappd_id=beer_id), container="draft", price_amd=2800)
    return tap, replace(tap, container="bottle", price_amd=None)


def test_a_check_in_leaves_the_servings_of_a_hand_entered_beer_and_the_board_keeps_supplying_them():
    state = ready("untappd_checkins:tap-station")
    tap, bottle = board()
    merge(state, manual(tap, bottle), now=NOW - 12 * H)
    merge(state, venue_checkins(checkin(5)))       # the check-in takes the pair over; on its own it must not wipe the list
    info = state.pairs["tap-station"]["u:5"].info
    assert (info["kind"], info["source"], info["serving"], info["name"]) == (
        "checkin", "untappd_checkins", "Draft", "Ale 5")              # everything else is the check-in's, as before
    assert [(s["container"], s["price_amd"]) for s in info["servings"]] == [("draft", 2800), ("bottle", None)]
    assert (info["container"], info["price_amd"]) == ("draft", 2800)
    can = replace(tap, container="can", price_amd=900)                # the board grows: the pair follows it
    merge(state, venue_checkins(checkin(5)), manual(tap, bottle, can))
    assert [s["container"] for s in info["servings"]] == ["draft", "bottle", "can"]


def test_a_board_gives_servings_to_a_pair_a_check_in_already_owns():
    state = ready("untappd_checkins:tap-station")
    merge(state, venue_checkins(checkin(5)), now=NOW - 12 * H)
    tap, bottle = board()
    merge(state, venue_checkins(checkin(5)), manual(tap, bottle))
    info = state.pairs["tap-station"]["u:5"].info
    assert (info["kind"], info["name"]) == ("checkin", "Ale 5")
    assert [s["container"] for s in info["servings"]] == ["draft", "bottle"]
    assert (info["container"], info["price_amd"]) == ("draft", 2800)


def test_a_board_that_shrinks_to_one_serving_takes_the_list_off_a_check_in_owned_pair():
    state = ready("untappd_checkins:tap-station")
    tap, bottle = board()
    merge(state, manual(tap, bottle), now=NOW - 12 * H)
    merge(state, venue_checkins(checkin(5)), manual(tap, bottle))
    merge(state, venue_checkins(checkin(5)), manual(replace(bottle, price_amd=1500)))   # only a priced bottle is left
    info = state.pairs["tap-station"]["u:5"].info
    assert "servings" not in info and info["kind"] == "checkin"
    assert (info["container"], info["price_amd"]) == ("bottle", 1500)


def test_a_single_serving_board_leaves_a_check_in_owned_pair_as_it_was():
    state = ready("untappd_checkins:tap-station")
    merge(state, venue_checkins(checkin(5)), now=NOW - 12 * H)
    tap, _ = board()
    merge(state, venue_checkins(checkin(5)), manual(tap))
    info = state.pairs["tap-station"]["u:5"].info
    assert not {"container", "price_amd", "volume_ml", "servings"} & set(info)   # a beer with one serving shows as before


# --- entries of several friends for one beer: every id belongs to the pair -----------------------------

def test_a_pair_from_entries_of_several_friends_keeps_all_their_ids_while_they_last():
    state = empty_state(NOW - 10 * DAY)
    first, friend = entry("tap-station", "Hazy Pale", 2, by="Аня"), entry("tap-station", "Hazy Pale", 1, by="Ваня")
    merge(state, manual(first, friend))
    info = state.pairs["tap-station"]["n:379 hazy pale"].info
    assert (info["manual_id"], info["manual_ids"]) == (first.id, [first.id, friend.id])
    merge(state, manual(first))                    # the friend's entry was withdrawn
    assert info["manual_id"] == first.id and "manual_ids" not in info


def test_a_new_pair_from_entries_one_of_which_was_announced_is_not_announced_again():
    state = empty_state(NOW - 10 * DAY)
    first, friend = entry("tap-station", "Hazy Pale", 2, by="Аня"), entry("tap-station", "Hazy Pale", 1, by="Ваня")
    state.announced_manual = [friend.id]      # the friend's entry went out in a digest, say under another key
    out = merge(state, manual(first, friend))
    assert out.events == []
    assert state.pairs["tap-station"]["n:379 hazy pale"].notified_at == "suppressed"
