"""Integration tests for one run: real sources and rules on fixtures, fake web, Telegram and git."""
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from taps import run as run_mod
from taps.config import Config, ConfigError, Place, Settings
from taps.digest import build_digest
from taps.fetch import FetchError, HttpResponse, UntappdClient
from taps.gitsync import CheckoutError, commit_and_push, pull_ff
from taps.model import SourceResult
from taps.rules import MergeOutcome
from taps.run import Deps, edit_last_digest, main, run, update_alerts
from taps.sources.local_match import KnownBeer
from taps.sources.parma import fetch_parma as real_fetch_parma
from taps.state import (
    BeerRec, BreweryNewRec, PairRec, ShopMatchRec, UntappdRec, VenueRec, empty_state, load_state, save_state,
)
from taps.telegram import MAX_TEXT, Alerter, SendOutcome, send_message
from taps.timeutil import iso
from tests.helpers import fixture_json, fixture_text

CONFIG_FIXTURES = Path(__file__).parent / "fixtures" / "config"   # frozen: never the live hand-edited files
NOW = datetime(2026, 9, 24, 14, 17, tzinfo=timezone.utc)       # Thu 18:17 in Yerevan
NEXT_EVENING = NOW + timedelta(days=1)
MONDAY_MORNING = datetime(2026, 9, 28, 5, 0, tzinfo=timezone.utc)   # Mon 09:00 in Yerevan
ENV = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "-100chat", "TELEGRAM_ADMIN_CHAT_ID": "42",
       "SITE_URL": "https://example.github.io/yerevan-taps/"}

PLACES_YAML = """
places:
  - {id: gargoyle, name: Gargoyle Bar, kind: bar, sources: {untappd_menu: {slug: gargoyle-bar, venue_id: 12252462}}}
  - {id: beatles, name: Beatles Pub, kind: bar, sources: {untappd_menu: {slug: beatles-pub-yerevan, venue_id: 2162817}}}
  - id: dargett-brewpub
    name: Dargett Brewpub
    kind: brewpub
    brewery_id: 265165
    brewery_name: Dargett
    untappd_venue_id: 4640403
    sources: {buyam: {url: "https://buy.am/en/restaurants/dargett"}}
  - {id: craft-story, name: Craft Story, kind: bar, sources: {untappd_checkins: {slug: craft-story, venue_id: 12281551}}}
  - id: closed-bar
    name: Closed Bar
    kind: bar
    enabled: false
    sources: {untappd_checkins: {slug: closed-bar, venue_id: 88888}}
  - {id: beer-city, name: Beer City, kind: shop, sources: {beercity: {}}}
  - {id: yerevan-city, name: Yerevan City, kind: shop, sources: {yerevan_city: {}}}
  - {id: parma, name: Parma, kind: shop, sources: {parma: {}}}
breweries:
  - {id: dargett, name: Dargett, brewery_id: 265165, slug: dargett-brewery}
"""
SOURCE_KEYS = {"untappd_menu:gargoyle", "untappd_menu:beatles", "untappd_brewery:265165",
               "untappd_checkins:craft-story", "beercity:beer-city", "yerevan_city:yerevan-city",
               "parma:parma", "buyam:dargett-brewpub", "manual"}

# --- fake Untappd (pages by URL, as the Playwright fetcher returns them) ------------------------

GARGOYLE = "https://untappd.com/v/gargoyle-bar/12252462"
BEATLES = "https://untappd.com/v/beatles-pub-yerevan/2162817"
BEATLES_HTML = fixture_text("untappd/beatles_menu.html")
EXTRA_LI = ('<li class="menu-item" id="beer"><div class="beer-details"><h5>'
            '<a href="/b/zagovor-black-sails/999001">Black Sails</a><em>Imperial Stout</em></h5>'
            '<h6><span>11% ABV • <a href="/w/zagovor/777">Zagovor</a></span>'
            '<div class="caps small" data-rating="4.12"></div></h6></div></li>')
FIRST_LI = '<li class="menu-item" id="beer">'
UNTAPPD_PAGES = {
    GARGOYLE: fixture_text("untappd/gargoyle_menu.html"),
    GARGOYLE + "?menu_id=203568": fixture_text("untappd/gargoyle_menu_tab.html"),
    BEATLES: BEATLES_HTML,
    "https://untappd.com/brewery/265165": fixture_text("untappd/dargett_brewery.html"),
    "https://untappd.com/v/craft-story/12281551": fixture_text("untappd/craftstory_checkins.html"),
}
WITH_EXTRA_BEER = {**UNTAPPD_PAGES, BEATLES: BEATLES_HTML.replace(FIRST_LI, EXTRA_LI + FIRST_LI, 1)}
CHALLENGE = HttpResponse(403, {"cf-mitigated": "challenge", "server": "cloudflare"},
                         fixture_text("untappd/cloudflare_challenge.html"))
CLOUDFLARE_BODY = fixture_text("untappd/cloudflare_challenge.html")   # a 200 page whose body is the challenge

# v1.1 city check (§4): a foreign venue's page, JSON-LD only (no venue-header: logo/verified stay unset)
FOREIGN_VENUE_HTML = """
<script type="application/ld+json">
{"@context":"http:\\/\\/schema.org\\/","@type":"Location","name":"Old Tbilisi Brewery",
 "address":{"@type":"PostalAddress","streetAddress":"1 Rustaveli Ave","addressLocality":"Tbilisi",
 "addressCountry":"Georgia"}}
</script>
"""


class FakeUntappd:
    def __init__(self, pages=UNTAPPD_PAGES, challenge=False):
        self.pages, self.challenge = pages, challenge
        self.started = self.closed = 0
        self.urls = []

    def __call__(self):
        self.started += 1
        return self.fetch_page, self.close

    def fetch_page(self, url):
        self.urls.append(url)
        if self.challenge:
            return CHALLENGE
        return HttpResponse(200, {}, self.pages[url]) if url in self.pages else HttpResponse(404, {}, "")

    def close(self):
        self.closed += 1


# --- fake shops (Http by URL) -------------------------------------------------------------------

def _single_page(listing_json, page, pages):
    return listing_json.replace(f"Page <b>{page}</b> of {pages}", "Page <b>1</b> of 1")


def _renumber(html, prefix):
    return re.sub(r"_(\d+)(?=[\"'])", lambda m: f"_{prefix}{m.group(1)}", html)


PARMA_P1 = fixture_text("parma/list_p1.html")
BUYAM_NAMES = ["Bohemian Pilsner", "Bavarian Weizen", "Oatmeal Stout", "Munich Lager", "Vienna Lager",
               "Biere Blanche", "Apricot Ale", "Belgian Tripel", "American Pale Ale", "Session IPA",
               "India Pale Ale", "Black IPA", "Apple Cider", "Cherry Ale", "Baltic Porter", "Imperial IPA"]
BUYAM_LISTING = json.dumps({"code": 200, "data": {"totalCount": 16, "items": [
    {"id": 174894 + i, "name": f"Draught beer Dargett {n} 1l", "nameEn": f"Draught beer Dargett {n} 1l",
     "basePrice": 2500,
     "imagesPaths": {"large": "media/image/01/ce/96/Dargett_1l.webp",
                     "small": "media/image/02/b8/1b/Dargett_1l_200x200@2x.webp"}}
    for i, n in enumerate(BUYAM_NAMES)]}})
SHOP_PAGES = {
    # Beer City: one page per category (18 beers), counters rewritten to "1 of 1"
    "https://www.beer-city.am/en/catalog/sshalcavac-garejur/?sorting=-id&page=1":
        _single_page(fixture_text("beercity/list_bottles_p1.json"), 1, 23),
    "https://www.beer-city.am/en/catalog/lcnovi-garejur/?sorting=-id&page=1":
        _single_page(fixture_text("beercity/list_draft_last.json"), 3, 3),
    # Parma: pages 2-3 are renumbered copies of page 1 (187 cards)
    **{f"https://parma.am/en/product/category?slug=beer&available=false&page={n}": html for n, html in
       enumerate([PARMA_P1, _renumber(PARMA_P1, "2"), _renumber(PARMA_P1, "3"),
                  fixture_text("parma/list_p4.html")], 1)},
    "https://buy.am/en/restaurants/dargett": fixture_text("buyam/dargett.html"),
    "https://api.buy.am/products/listing?skip=0&s=1162&f=9890&take=100": BUYAM_LISTING,
}
PRODUCT_PAGE = re.compile(r"https://(www\.beer-city\.am/en/products/|parma\.am/en/product/product\?)")
YC_POSTS = {
    "https://apishopv2.yerevan-city.am/api/Product/GetByCategory": fixture_json("yerevan_city/by_category.json"),
    "https://apishopv2.yerevan-city.am/api/Product/Search": fixture_json("yerevan_city/search.json"),
}


class FakeHttp:
    def __init__(self):
        self.urls = []

    def get(self, url, headers=None):
        self.urls.append(url)
        if url in SHOP_PAGES:
            return HttpResponse(200, {}, SHOP_PAGES[url])
        if PRODUCT_PAGE.match(url):
            return HttpResponse(200, {}, "<html><body></body></html>")   # product pages: fields unknown
        raise FetchError("http", f"404 {url}")

    def post_json(self, url, payload, headers=None):
        self.urls.append(url)
        return YC_POSTS[url]


# --- fake Telegram and git ----------------------------------------------------------------------

class World:
    """A repo folder plus recording fakes; outcomes are scripted per call."""

    def __init__(self, tmp_path, untappd=None, send=(), push=(), get_updates=(), delete=()):
        self.repo = tmp_path
        (tmp_path / "site").mkdir(exist_ok=True)
        (tmp_path / "places.yaml").write_text(PLACES_YAML, encoding="utf-8")
        shutil.copy(CONFIG_FIXTURES / "corrections.yaml", tmp_path / "corrections.yaml")
        self.untappd = untappd or FakeUntappd()
        self.http = FakeHttp()
        self.sends, self.pushes, self.pulls, self.deletes, self.get_updates_calls = [], [], [], [], []
        self.send_outcomes, self.push_results = list(send), list(push)
        self.get_updates_pages, self.delete_results = list(get_updates), list(delete)

    def send(self, token, chat_id, text, button=None):
        self.sends.append({"token": token, "chat": chat_id, "text": text, "button": button})
        return self.send_outcomes.pop(0) if self.send_outcomes else SendOutcome("sent")

    def push(self, repo, paths, message):
        state = json.loads((repo / "state.json").read_text(encoding="utf-8"))
        self.pushes.append({"paths": list(paths), "state": state})
        return self.push_results.pop(0) if self.push_results else True

    def get_updates(self, token, offset):
        self.get_updates_calls.append({"token": token, "offset": offset})
        return self.get_updates_pages.pop(0) if self.get_updates_pages else []

    def delete_message(self, token, chat_id, message_id):
        self.deletes.append({"token": token, "chat": chat_id, "message_id": message_id})
        return self.delete_results.pop(0) if self.delete_results else SendOutcome("sent")

    def deps(self):
        return Deps(http=self.http, untappd_fetcher=self.untappd, send=self.send,
                    get_updates=self.get_updates, delete_message=self.delete_message,
                    pull=self.pulls.append, push=self.push, sleep=lambda s: None)

    def run(self, now, **kw):
        return run(self.repo, now, ENV, self.deps(), **kw)

    def state(self):
        return load_state(self.repo / "state.json", NOW)

    def edit_state(self, change):
        state = self.state()
        change(state)
        save_state(self.repo / "state.json", state)

    def next_run(self, untappd=None, send=(), push=(), get_updates=(), delete=()):
        """Fresh fakes for the next run in the same repo."""
        self.untappd = untappd or FakeUntappd()
        self.http = FakeHttp()
        self.sends, self.pushes, self.pulls, self.deletes, self.get_updates_calls = [], [], [], [], []
        self.send_outcomes, self.push_results = list(send), list(push)
        self.get_updates_pages, self.delete_results = list(get_updates), list(delete)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def first_run(world):
    assert world.run(NOW) == 0
    world.next_run(untappd=FakeUntappd(WITH_EXTRA_BEER))


# --- tests --------------------------------------------------------------------------------------

def test_first_run_is_silent_and_saves_state_and_site(world):
    assert world.run(NOW) == 0

    state = world.state()
    assert world.pulls == [world.repo]
    assert world.sends == []
    assert [p["paths"] for p in world.pushes] == [["state.json"]]
    assert set(state.sources) == SOURCE_KEYS
    assert all(state.sources[k].baseline_done and state.sources[k].last_ok == iso(NOW) for k in SOURCE_KEYS)
    pairs = [rec for recs in state.pairs.values() for rec in recs.values()]
    assert len(pairs) > 300 and {rec.notified_at for rec in pairs} == {"baseline"}
    assert "u:4473" in state.pairs["beatles"]                      # Guinness Draught from the Beatles menu
    assert len(state.pairs["dargett-brewpub"]) == 16
    assert state.untappd.last_attempt == iso(NOW)
    assert world.untappd.started == world.untappd.closed == 1
    assert state.alerts == {}
    assert state.corrections_snapshot is not None
    data = json.loads((world.repo / "site" / "data.json").read_text(encoding="utf-8"))
    assert data["generated_at"] == iso(NOW)
    assert [p["id"] for p in data["places"]][:2] == ["gargoyle", "beatles"]
    assert any(r["name"] == "Guinness Draught" for r in data["rows"])


def test_shop_rows_get_the_shops_photo_and_a_guessed_style_that_stays_out_of_the_pairs(world):
    """Fixtures through the four shop adapters, the merge and the site data: every shop's own photo reaches
    beer_logo, and a beer with no Untappd behind it gets a style guessed from its name -- on the site row only."""
    assert world.run(NOW) == 0
    rows = {(r["place_id"], r["name"]): r for r in
            json.loads((world.repo / "site" / "data.json").read_text(encoding="utf-8"))["rows"]}
    hard_root = rows[("beer-city", "Hard root Double IPA")]
    assert hard_root["beer_logo"] == "https://www.beer-city.am/media/product-img/small_3_wCYfvQC.jpg"
    assert (hard_root["style"], hard_root["style_inferred"]) == ("IPA", True)
    dahook = rows[("parma", "Dahook Ipa light")]
    assert dahook["beer_logo"] == "https://static.parma.am/cache/product/28051/thumb_28051.jpg?v=11790128223"
    assert (dahook["style"], dahook["style_inferred"]) == ("IPA", True)
    pilsner = rows[("dargett-brewpub", "Bohemian Pilsner")]
    assert pilsner["beer_logo"] == "https://buy.am/media/image/02/b8/1b/Dargett_1l_200x200@2x.webp"
    assert (pilsner["style"], pilsner["style_inferred"]) == ("Pilsner", True)
    yerevan = [r for (place, _), r in rows.items() if place == "yerevan-city"]
    assert yerevan and all(r["beer_logo"].startswith("https://media.yerevan-city.am/") for r in yerevan if r["beer_logo"])
    assert sum(1 for r in yerevan if r["beer_logo"]) > 0.9 * len(yerevan)
    state = world.state()
    assert "style" not in state.pairs["beer-city"]["n:hard root double ipa"].info      # the digest reads the pair
    assert state.pairs["beer-city"]["n:hard root double ipa"].info["logo"] == hard_root["beer_logo"]


def test_shop_country_backfill_reads_product_pages_only_of_known_items_never_checked(world):
    """A first run reads every product page once (fixtures carry no country, so all are then "checked");
    later runs read none until a pair loses its check mark, and then exactly that item's page."""
    assert world.run(NOW) == 0
    world.next_run()
    assert world.run(NOW + timedelta(hours=1)) == 0
    assert [u for u in world.http.urls if PRODUCT_PAGE.match(u)] == []

    def forget(state):
        for place, key in (("beer-city", "n:hard root double ipa"), ("parma", "n:dahook ipa light")):
            del state.pairs[place][key].info["country_checked"]
    world.edit_state(forget)
    world.next_run()
    assert world.run(NOW + timedelta(hours=2)) == 0
    assert sorted(u for u in world.http.urls if PRODUCT_PAGE.match(u)) == [
        "https://parma.am/en/product/product?slug=beer-dahook-ipa-light-330ml_28051",
        "https://www.beer-city.am/en/products/garejur-hard-rut-dabl-ipa-045l/"]
    world.next_run()
    assert world.run(NOW + timedelta(hours=3)) == 0
    assert [u for u in world.http.urls if PRODUCT_PAGE.match(u)] == []   # remembered: checked, no country
    info = world.state().pairs["parma"]["n:dahook ipa light"].info
    assert info["country_checked"] is True and "country" not in info


# --- v1.1: venue meta / discovery -----------------------------------------------------

def test_venue_meta_recorded_from_own_venue_pages(world):
    first_run(world)
    state = world.state()
    gargoyle = state.venues["12252462"]
    assert (gargoyle.name, gargoyle.verified) == ("Gargoyle Bar", True)
    assert gargoyle.logo and gargoyle.logo.startswith("https://")
    assert state.venues["12281551"].name == "Craft Story"   # craft-story's own untappd_checkins page


def test_weekly_discovery_report_lists_untracked_venues_with_enough_checkins(world):
    first_run(world)

    def add_venues(state):
        state.venues["99999"] = VenueRec(
            name="KER U SUS", url="https://untappd.com/v/ker-u-sus/99999", city="Yerevan", country="Armenia",
            checkins=[{"id": 1, "at": iso(MONDAY_MORNING - timedelta(days=1))},
                     {"id": 2, "at": iso(MONDAY_MORNING - timedelta(days=2))},
                     {"id": 3, "at": iso(MONDAY_MORNING - timedelta(days=3))}])
        state.venues["55555"] = VenueRec(   # below the 3-checkin threshold: not reported
            name="Too Few", url="https://untappd.com/v/too-few/55555",
            checkins=[{"id": 9, "at": iso(MONDAY_MORNING - timedelta(days=1))}])
    world.edit_state(add_venues)
    world.next_run()

    assert world.run(MONDAY_MORNING) == 0

    admin_sends = [s for s in world.sends if s["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]]
    discovery = next(s for s in admin_sends if "Новые места по чекинам" in s["text"])
    assert "KER U SUS" in discovery["text"] and "3 чекина" in discovery["text"]
    assert "untappd.com/v/ker-u-sus/99999" in discovery["text"]
    assert "Too Few" not in discovery["text"]
    assert "Добавить в список" in discovery["text"]
    state = world.state()
    assert state.discovery.reported == [99999]
    assert state.discovery.last_report_date == "2026-09-28"
    # the report was pushed before it was sent, like a digest
    assert world.pushes[0]["state"]["discovery"]["reported"] == [99999]

    # a second run the same week does not repeat it
    world.next_run()
    assert world.run(MONDAY_MORNING + timedelta(hours=1)) == 0
    assert not any("Новые места по чекинам" in s["text"] for s in world.sends)


def test_weekly_discovery_report_marks_the_week_checked_even_when_empty(world):
    first_run(world)
    assert world.run(MONDAY_MORNING) == 0
    assert not any("Новые места по чекинам" in s["text"] for s in world.sends)
    assert world.state().discovery.last_report_date == "2026-09-28"


def test_weekly_discovery_report_skips_a_venue_already_reported(world):
    first_run(world)
    world.edit_state(lambda s: setattr(s.discovery, "reported", [99999]))

    def add_venue(state):
        state.venues["99999"] = VenueRec(
            name="KER U SUS", url="https://untappd.com/v/ker-u-sus/99999",
            checkins=[{"id": i, "at": iso(MONDAY_MORNING - timedelta(days=1))} for i in range(5)])
    world.edit_state(add_venue)
    world.next_run()

    assert world.run(MONDAY_MORNING) == 0

    assert not any("Новые места по чекинам" in s["text"] for s in world.sends)


# --- v1.1 discovery_daily_until: owner's temporary daily cadence for the discovery report --------

def _config(places=None, **settings_kw):
    return Config(places=places or {}, breweries=(), settings=Settings(**settings_kw))


def test_discovery_due_daily_the_first_time_that_day():
    state = empty_state(NOW)
    config = _config(discovery_daily_until="2026-09-30")
    assert run_mod._discovery_due(state, config, NOW) is True


def test_discovery_not_due_daily_again_the_same_day():
    state = empty_state(NOW)
    config = _config(discovery_daily_until="2026-09-30")
    state.discovery.last_report_date = run_mod._discovery_period(config, NOW)
    assert run_mod._discovery_due(state, config, NOW) is False


def test_discovery_due_daily_again_the_next_day():
    state = empty_state(NOW)
    config = _config(discovery_daily_until="2026-09-30")
    state.discovery.last_report_date = run_mod._discovery_period(config, NOW)
    assert run_mod._discovery_due(state, config, NOW + timedelta(days=1)) is True


def test_discovery_not_due_daily_before_9am_yerevan():
    state = empty_state(NOW)
    config = _config(discovery_daily_until="2026-09-30")
    before_9am = datetime(2026, 9, 24, 2, 0, tzinfo=timezone.utc)   # 06:00 in Yerevan, same Yerevan date as NOW
    assert run_mod._discovery_due(state, config, before_9am) is False


def test_discovery_falls_back_to_weekly_once_the_daily_window_expires():
    """Once the Yerevan date passes discovery_daily_until, the report is due once per week again
    (not once per day): reporting on Thursday must not make Friday, the same week, due again."""
    config = _config(discovery_daily_until="2026-09-23")   # expired: NOW's Yerevan date (09-24) is past it
    state = empty_state(NOW)
    assert run_mod._discovery_due(state, config, NOW) is True   # Thursday: first check of the week
    state.discovery.last_report_date = run_mod._discovery_period(config, NOW)

    assert run_mod._discovery_due(state, config, NOW + timedelta(days=1)) is False   # Friday, same week

    assert run_mod._discovery_due(state, config, MONDAY_MORNING) is True   # next week's Monday


def test_disabled_place_venue_is_not_reported_as_a_new_place(world):
    """I-2: a disabled place's venue counts as tracked/known and must never reach the discovery report."""
    first_run(world)

    def add_venue(state):
        state.venues["88888"] = VenueRec(
            name="Closed Bar", url="https://untappd.com/v/closed-bar/88888",
            checkins=[{"id": i, "at": iso(MONDAY_MORNING - timedelta(days=1))} for i in range(5)])
    world.edit_state(add_venue)
    world.next_run()

    assert world.run(MONDAY_MORNING) == 0

    assert not any("Closed Bar" in s["text"] for s in world.sends)


def test_prune_drops_untracked_venue_record_without_recent_checkins(world):
    """I-1: worldwide venues seen once on a brewery page must not accumulate in state.venues forever."""
    first_run(world)
    world.edit_state(lambda s: s.venues.__setitem__(
        "77777", VenueRec(name="Random Bar", url="u", checkins=[{"id": 1, "at": iso(NOW - timedelta(days=70))}])))
    world.next_run()

    assert world.run(NEXT_EVENING) == 0

    assert "77777" not in world.state().venues


def test_prune_keeps_a_disabled_known_place_venue_without_recent_checkins(world):
    """I-1: a disabled place's own venue is kept (for its logo/verified) even through a quiet spell."""
    first_run(world)
    world.edit_state(lambda s: s.venues.__setitem__(
        "88888", VenueRec(name="Closed Bar", url="u", checkins=[{"id": 1, "at": iso(NOW - timedelta(days=70))}])))
    world.next_run()

    assert world.run(NEXT_EVENING) == 0

    assert "88888" in world.state().venues


# --- v1.1: city check for discovered venues (§4) ------------------------------------

def test_city_check_learns_an_armenian_venues_location_and_never_rechecks_it(world):
    first_run(world)

    def add_venue(state):
        state.venues["70001"] = VenueRec(
            name="KER U SUS", url="https://untappd.com/v/ker-u-sus/70001",
            checkins=[{"id": i, "at": iso(NEXT_EVENING - timedelta(days=1))} for i in range(2)])
    world.edit_state(add_venue)
    pages = {**UNTAPPD_PAGES, "https://untappd.com/v/ker-u-sus/70001": fixture_text("untappd/vertigo_checkins.html")}
    world.next_run(untappd=FakeUntappd(pages))

    assert world.run(NEXT_EVENING) == 0

    rec = world.state().venues["70001"]
    assert rec.country == "Armenia" and rec.city and rec.location_checked_at == iso(NEXT_EVENING)
    assert rec.logo and rec.verified is True   # vertigo_checkins.html's own venue-header, read along the way

    # a second run, well past LOCATION_RECHECK_DAYS, must not fetch it again: the location is known
    world.next_run(untappd=FakeUntappd(pages))
    later = NEXT_EVENING + timedelta(days=200)
    assert world.run(later) == 0
    assert "https://untappd.com/v/ker-u-sus/70001" not in world.untappd.urls


def test_city_check_marks_a_foreign_venue_and_excludes_it_from_report_and_site(world):
    first_run(world)

    def add_venue(state):
        state.venues["54321"] = VenueRec(
            name="Old Tbilisi Brewery", url="https://untappd.com/v/old-tbilisi/54321",
            checkins=[{"id": i, "at": iso(MONDAY_MORNING - timedelta(days=1))} for i in range(5)])
    world.edit_state(add_venue)
    pages = {**UNTAPPD_PAGES, "https://untappd.com/v/old-tbilisi/54321": FOREIGN_VENUE_HTML}
    world.next_run(untappd=FakeUntappd(pages))

    assert world.run(MONDAY_MORNING, no_digest=True) == 0

    state = world.state()
    assert state.venues["54321"].country == "Georgia" and state.venues["54321"].city == "Tbilisi"
    assert not any("Old Tbilisi" in s["text"] for s in world.sends)
    data = json.loads((world.repo / "site" / "data.json").read_text(encoding="utf-8"))
    assert not any(v["name"] == "Old Tbilisi Brewery" for v in data["venues"])


def test_city_check_picks_up_to_3_untracked_venues_most_checkins_first(world):
    first_run(world)
    counts = [5, 4, 3, 2, 1]   # only the top 3 by count qualify as candidates and fit the per-run cap

    def add_venues(state):
        for i, n in enumerate(counts):
            vid = 80000 + i
            state.venues[str(vid)] = VenueRec(
                name=f"Venue {i}", url=f"https://untappd.com/v/venue-{i}/{vid}",
                checkins=[{"id": j, "at": iso(NEXT_EVENING - timedelta(days=1))} for j in range(n)])
    world.edit_state(add_venues)
    pages = {**UNTAPPD_PAGES, **{f"https://untappd.com/v/venue-{i}/{80000 + i}": fixture_text("untappd/gargoyle_menu.html")
                                 for i in range(len(counts))}}
    world.next_run(untappd=FakeUntappd(pages))

    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    checked = {i: state.venues[str(80000 + i)].location_checked_at is not None for i in range(len(counts))}
    assert checked == {0: True, 1: True, 2: True, 3: False, 4: False}


def test_city_check_stops_on_a_cloudflare_block_without_marking_checked(world):
    first_run(world)

    def add_venue(state):
        state.venues["54321"] = VenueRec(
            name="Blocked Venue", url="https://untappd.com/v/blocked/54321",
            checkins=[{"id": i, "at": iso(NEXT_EVENING - timedelta(days=1))} for i in range(2)])
    world.edit_state(add_venue)
    pages = {**UNTAPPD_PAGES, "https://untappd.com/v/blocked/54321": CLOUDFLARE_BODY}
    world.next_run(untappd=FakeUntappd(pages))

    assert world.run(NEXT_EVENING) == 0

    rec = world.state().venues["54321"]
    assert rec.city is None and rec.country is None and rec.location_checked_at is None
    assert "https://untappd.com/v/blocked/54321" in world.untappd.urls   # it was attempted


def test_city_check_marks_a_gyumri_venue_but_excludes_it_like_a_foreign_one(world):
    """I-3 + M-1: addressLocality with no addressCountry (e.g. "Gyumri Հայաստան") yields country
    Armenia (M-1), but the venue is still hidden from the report and the site -- only Yerevan counts."""
    first_run(world)
    gyumri_html = ('<script type="application/ld+json">'
                  '{"@type":"Location","address":{"addressLocality":"Gyumri Հայաստան"}}</script>')

    def add_venue(state):
        state.venues["54322"] = VenueRec(
            name="Gyumri Brewhouse", url="https://untappd.com/v/gyumri-brewhouse/54322",
            checkins=[{"id": i, "at": iso(MONDAY_MORNING - timedelta(days=1))} for i in range(5)])
    world.edit_state(add_venue)
    pages = {**UNTAPPD_PAGES, "https://untappd.com/v/gyumri-brewhouse/54322": gyumri_html}
    world.next_run(untappd=FakeUntappd(pages))

    assert world.run(MONDAY_MORNING, no_digest=True) == 0

    rec = world.state().venues["54322"]
    assert (rec.city, rec.country) == ("Gyumri", "Armenia")
    assert not any("Gyumri Brewhouse" in s["text"] for s in world.sends)
    data = json.loads((world.repo / "site" / "data.json").read_text(encoding="utf-8"))
    assert not any(v["name"] == "Gyumri Brewhouse" for v in data["venues"])


def test_city_check_survives_an_unparseable_venue_page(world):
    """I-1: an unexpected page (JSON-LD that is a list of plain strings, no Location object at all)
    must fail this one venue only -- run() completes, the venue is marked checked, and every other
    candidate that same run is unaffected."""
    first_run(world)

    def add_venues(state):
        state.venues["54323"] = VenueRec(
            name="Weird Page", url="https://untappd.com/v/weird/54323",
            checkins=[{"id": i, "at": iso(NEXT_EVENING - timedelta(days=1))} for i in range(3)])
        state.venues["54324"] = VenueRec(
            name="KER U SUS", url="https://untappd.com/v/ker-u-sus/54324",
            checkins=[{"id": i, "at": iso(NEXT_EVENING - timedelta(days=1))} for i in range(2)])
    world.edit_state(add_venues)
    weird_html = '<script type="application/ld+json">["just", "a", "list", "of", "strings"]</script>'
    pages = {**UNTAPPD_PAGES, "https://untappd.com/v/weird/54323": weird_html,
             "https://untappd.com/v/ker-u-sus/54324": fixture_text("untappd/vertigo_checkins.html")}
    world.next_run(untappd=FakeUntappd(pages))

    assert world.run(NEXT_EVENING) == 0

    weird = world.state().venues["54323"]
    assert weird.city is None and weird.country is None and weird.location_checked_at == iso(NEXT_EVENING)
    other = world.state().venues["54324"]
    assert other.city == "Yerevan" and other.location_checked_at == iso(NEXT_EVENING)


def test_discover_venue_locations_survives_a_parse_crash_and_continues(monkeypatch):
    """I-1, unit-level: any parse error (not just the type-safety cases above) must fail only that
    candidate -- it is marked checked, and the loop continues to the next one instead of raising."""
    state = empty_state(NOW)
    state.venues["1"] = VenueRec(name="Boom", url="https://untappd.com/v/boom/1",
                                 checkins=[{"id": i, "at": iso(NOW - timedelta(days=1))} for i in range(2)])
    state.venues["2"] = VenueRec(name="Fine", url="https://untappd.com/v/fine/2",
                                 checkins=[{"id": i, "at": iso(NOW - timedelta(days=1))} for i in range(2)])
    config = Config(places={}, breweries=(), settings=Settings())

    class FakeLocClient:
        def __init__(self, pages):
            self.pages = pages

        def get(self, url):
            return self.pages[url]

    client = FakeLocClient({"https://untappd.com/v/boom/1": "<html></html>",
                           "https://untappd.com/v/fine/2": "<html></html>"})
    calls = []

    def fake_parse(html):
        calls.append(html)
        if len(calls) == 1:
            raise ValueError("boom")
        return None
    monkeypatch.setattr(run_mod, "parse_venue_location", fake_parse)

    run_mod.discover_venue_locations(state, config, client, NOW)

    assert state.venues["1"].location_checked_at == iso(NOW)   # marked checked despite the crash
    assert state.venues["2"].location_checked_at == iso(NOW)   # loop continued to the next candidate


def test_location_known_via_country_only_is_not_a_recheck_candidate(world):
    """M-4: a location is "known" once either city or country is set -- an address with only
    addressCountry (no addressLocality) must not look unchecked and get re-fetched every run."""
    state = empty_state(NOW)
    state.venues["54325"] = VenueRec(
        name="Country Only", url="https://untappd.com/v/country-only/54325", city=None, country="Georgia",
        location_checked_at=iso(NOW - timedelta(days=200)),
        checkins=[{"id": i, "at": iso(NOW - timedelta(days=1))} for i in range(5)])
    save_state(world.repo / "state.json", state)

    assert world.run(NOW) == 0

    assert "https://untappd.com/v/country-only/54325" not in world.untappd.urls


def test_city_check_respects_the_untappd_budget(world):
    """The daily budget runs out during the main jobs (as in test_sources_out_of_untappd_budget_are_
    skipped_without_failure): the city check must not spend a page either, and stays unchecked."""
    state = empty_state(NOW)
    state.untappd.pages_today, state.untappd.pages_date = 39, "2026-09-24"   # 1 of 40 pages left
    state.venues["54321"] = VenueRec(
        name="Never Reached", url="https://untappd.com/v/never/54321",
        checkins=[{"id": i, "at": iso(NOW - timedelta(days=1))} for i in range(2)])
    save_state(world.repo / "state.json", state)

    assert world.run(NOW) == 0

    rec = world.state().venues["54321"]
    assert rec.location_checked_at is None
    assert world.untappd.urls == [GARGOYLE]   # the one page the budget allowed; nothing after it


# --- v1.1 §2: beer ratings for beers seen only in check-ins ------------------

BEER_URL = "https://untappd.com/b/dargett-brewery-cherry-ale-morello/1559917"
BEER_PAGE_HTML = fixture_text("untappd/beer_page.html")


def _checkin_pair(checkin_at, url="https://untappd.com/b/x/1", last_seen=None, **extra):
    return PairRec(first_seen=iso(NOW), last_seen=last_seen or iso(NOW),
                   info={"kind": "checkin", "checkin_at": checkin_at, "url": url, **extra})


def _untappd_client(pages, daily_pages=30, untappd=None):
    def fetch_page(url):
        return HttpResponse(200, {}, pages[url]) if url in pages else HttpResponse(404, {}, "")
    return UntappdClient(untappd or UntappdRec(), daily_pages, NOW, fetch_page, sleep=lambda s: None)


REAL_BEER_URL = "https://untappd.com/b/brouwerij-rodenbach-rodenbach-fruitage/1715344"
REAL_BEER_PAGE = fixture_text("untappd/beer_page_real.html")


def _manual_pair(**info):
    return PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "manual", "name": "X", **info})


def test_fetch_bar_beer_pages_caches_label_and_country_from_a_real_page():
    state = empty_state(NOW)
    state.pairs = {"t": {"u:1715344": _checkin_pair(iso(NOW - timedelta(days=1)), url=REAL_BEER_URL)}}
    run_mod.fetch_bar_beer_pages(state, _config(), _untappd_client({REAL_BEER_URL: REAL_BEER_PAGE}), NOW)
    beer = state.beers["u:1715344"]
    assert (beer.rating, beer.country) == (3.48902, "Belgium")
    assert beer.logo == "https://assets.untappd.com/site/beer_logos/beer-1715344_b8fec_sm.jpeg"
    assert (beer.name, beer.brewery) == ("Rodenbach Fruitage", "Brouwerij Rodenbach")


def test_bar_beer_candidates_picks_a_checkin_lacking_style():
    state = empty_state(NOW)
    state.pairs = {"gargoyle": {
        "u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1",
                             logo="https://assets.untappd.com/a.jpg", rating=4.0),   # style still missing
    }}
    config = _config(places={"gargoyle": Place(id="gargoyle", name="Gargoyle Bar", kind="bar", sources={})})
    assert run_mod._bar_beer_candidates(state, config, NOW) == [("u:1", "https://untappd.com/b/x/1")]


def test_bar_beer_candidates_skips_a_beer_with_label_rating_and_style():
    state = empty_state(NOW)
    state.pairs = {"gargoyle": {
        "u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1",
                             logo="https://assets.untappd.com/a.jpg", rating=4.0, style="Stout"),
    }}
    config = _config(places={"gargoyle": Place(id="gargoyle", name="Gargoyle Bar", kind="bar", sources={})})
    assert run_mod._bar_beer_candidates(state, config, NOW) == []


def test_bar_beer_candidates_skips_a_beer_page_read_recently():
    state = empty_state(NOW)
    state.pairs = {"gargoyle": {"u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1")}}
    state.beers["u:1"] = BeerRec(first_seen_city=iso(NOW), rating_at=iso(NOW - timedelta(days=2)))
    config = _config(places={"gargoyle": Place(id="gargoyle", name="Gargoyle Bar", kind="bar", sources={})})
    assert run_mod._bar_beer_candidates(state, config, NOW) == []


def test_bar_beer_candidates_excludes_a_shop_places_checkin():
    """A shop that is also an Untappd check-in venue (e.g. Houl) belongs to tier B, not tier A."""
    state = empty_state(NOW)
    state.pairs = {"houl": {"u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1")}}
    config = _config(places={"houl": Place(id="houl", name="Houl", kind="shop", sources={})})
    assert run_mod._bar_beer_candidates(state, config, NOW) == []


def test_bar_beer_candidates_includes_a_manual_pair_missing_a_label():
    state = empty_state(NOW)
    state.pairs = {"ferment": {"u:1715344": _manual_pair(brewery="Rodenbach")}}
    config = _config(places={"ferment": Place(id="ferment", name="Ferment", kind="bar", sources={})})
    assert run_mod._bar_beer_candidates(state, config, NOW) == [
        ("u:1715344", "https://untappd.com/beer/1715344")]


def test_bar_beer_candidates_includes_a_checkin_whose_last_in_result_went_stale():
    """Bug B: a venue's own check-in page lists only its most recent check-ins, so
    rules._sightings' full-result reconciliation can reset last_in_result to False for an older
    check-in that Untappd no longer shows on that page -- yet site_data._visible still shows it by
    checkin_at age alone (kind == "checkin" never looks at last_in_result). The candidate tier must
    use the same rule, or such a beer is never read at all."""
    state = empty_state(NOW)
    pair = _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1")
    pair.last_in_result = False
    state.pairs = {"ferment": {"u:1": pair}}
    config = _config(places={"ferment": Place(id="ferment", name="Ferment", kind="bar", sources={})})
    assert run_mod._bar_beer_candidates(state, config, NOW) == [("u:1", "https://untappd.com/b/x/1")]


def test_bar_beer_candidates_excludes_a_manual_pair_no_longer_in_the_boards_result():
    """Unlike check-ins, a manual pair's visibility genuinely depends on last_in_result
    (site_data._visible checks it for kind == "manual"), so it must still gate here."""
    state = empty_state(NOW)
    pair = _manual_pair(brewery="Rodenbach")
    pair.last_in_result = False
    state.pairs = {"ferment": {"u:1715344": pair}}
    config = _config(places={"ferment": Place(id="ferment", name="Ferment", kind="bar", sources={})})
    assert run_mod._bar_beer_candidates(state, config, NOW) == []


def test_bar_beer_candidates_most_recently_seen_first_capped_at_thirty():
    state = empty_state(NOW)
    state.pairs = {"gargoyle": {
        f"u:{i}": _checkin_pair(iso(NOW - timedelta(days=1)), url=f"https://untappd.com/b/x/{i}",
                                last_seen=iso(NOW - timedelta(days=i)))
        for i in range(1, 32)
    }}
    config = _config(places={"gargoyle": Place(id="gargoyle", name="Gargoyle Bar", kind="bar", sources={})})
    candidates = run_mod._bar_beer_candidates(state, config, NOW)
    assert len(candidates) == 30
    assert [key for key, _ in candidates][:3] == ["u:1", "u:2", "u:3"]


def test_shop_beer_page_candidates_picks_a_shop_places_checkin_lacking_details():
    """v1.3: a shop that is also an Untappd check-in venue (e.g. Houl) is tier B, not tier A."""
    state = empty_state(NOW)
    state.pairs = {"houl": {"u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1")}}
    config = _config(places={"houl": Place(id="houl", name="Houl", kind="shop", sources={})})
    assert run_mod._shop_beer_page_candidates(state, config, NOW) == [("u:1", "https://untappd.com/b/x/1")]


def test_shop_beer_page_candidates_includes_a_checkin_whose_last_in_result_went_stale():
    """Same bug as _bar_beer_candidates: a shop-place venue's own check-in page lists only its most
    recent check-ins, so last_in_result can go stale for an older but still-shown check-in even
    though site_data._visible shows it by checkin_at age alone."""
    state = empty_state(NOW)
    pair = _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1")
    pair.last_in_result = False
    state.pairs = {"houl": {"u:1": pair}}
    config = _config(places={"houl": Place(id="houl", name="Houl", kind="shop", sources={})})
    assert run_mod._shop_checkin_candidates(state, config, NOW) == [("u:1", "https://untappd.com/b/x/1")]
    assert run_mod._shop_beer_page_candidates(state, config, NOW) == [("u:1", "https://untappd.com/b/x/1")]


def test_shop_beer_page_candidates_skips_a_shop_checkin_with_full_details():
    state = empty_state(NOW)
    state.pairs = {"houl": {"u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1",
                                                 logo="https://assets.untappd.com/a.jpg", rating=4.0, style="Stout")}}
    config = _config(places={"houl": Place(id="houl", name="Houl", kind="shop", sources={})})
    assert run_mod._shop_beer_page_candidates(state, config, NOW) == []


def test_shop_beer_page_candidates_excludes_a_stale_shop_checkin():
    state = empty_state(NOW)
    state.pairs = {"houl": {"u:1": _checkin_pair(iso(NOW - timedelta(days=22)))}}
    config = _config(places={"houl": Place(id="houl", name="Houl", kind="shop", sources={})})
    assert run_mod._shop_beer_page_candidates(state, config, NOW) == []


def test_shop_beer_page_candidates_combines_matched_names_and_shop_checkins():
    state = empty_state(NOW)
    state.pairs = {
        "houl": {"u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1")},
        "beer-city": {"n:rodenbach": _shop_pair("Rodenbach", "Rodenbach fruitage")},
    }
    config = _config(places={"houl": Place(id="houl", name="Houl", kind="shop", sources={}),
                             "beer-city": Place(id="beer-city", name="Beer City", kind="shop", sources={})})
    state.shop_matches["n:rodenbach"] = _nameless_match()
    got = run_mod._shop_beer_page_candidates(state, config, NOW)
    assert got == [("u:1715344", "https://untappd.com/beer/1715344"), ("u:1", "https://untappd.com/b/x/1")]


def test_shop_beer_page_candidates_capped_at_ten():
    state = empty_state(NOW)
    state.pairs = {"houl": {
        f"u:{i}": _checkin_pair(iso(NOW - timedelta(days=1)), url=f"https://untappd.com/b/x/{i}",
                                last_seen=iso(NOW - timedelta(days=i)))
        for i in range(1, 12)
    }}
    config = _config(places={"houl": Place(id="houl", name="Houl", kind="shop", sources={})})
    got = run_mod._shop_beer_page_candidates(state, config, NOW)
    assert len(got) == 10
    assert [key for key, _ in got][:2] == ["u:1", "u:2"]


def test_country_beer_page_candidates_capped_at_eight():
    state = empty_state(NOW)
    state.pairs = {"gargoyle": {
        f"u:{i}": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), last_in_result=True,
                         info={"kind": "menu", "brewery": f"Brewery{i}", "url": f"https://untappd.com/b/z/{i}"})
        for i in range(1, 10)
    }}
    assert len(run_mod._country_beer_page_candidates(state, NOW)) == 8


def _nameless_match(**kw):
    return ShopMatchRec(untappd_beer_id=1715344, url="https://untappd.com/beer/1715344", via="manual",
                        matched_at=iso(NOW), **kw)


@pytest.mark.parametrize("match, beer", [
    (_nameless_match(name="Rodenbach Fruitage"), None),                                        # name known
    (_nameless_match(), BeerRec(first_seen_city="x", name="Rodenbach Fruitage")),              # page already read
    (_nameless_match(), BeerRec(first_seen_city="x", rating_at=iso(NOW - timedelta(days=2)))),   # fetched recently
    (ShopMatchRec(untappd_beer_id=None, matched_at=iso(NOW)), None),                           # no_match / blocked
])
def test_matched_name_candidates_skip_matches_that_need_no_page(match, beer):
    state = empty_state(NOW)
    state.shop_matches["n:x"] = match
    if beer:
        state.beers["u:1715344"] = beer
    assert run_mod._matched_name_candidates(state, NOW) == []


def test_country_candidates_ask_for_a_country_once_per_brewery():
    state = empty_state(NOW)
    state.pairs = {"gargoyle": {
        "u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), last_in_result=True,
                       info={"kind": "menu", "brewery": "Zagovor", "logo": "https://assets.untappd.com/a.jpg", "url": "https://untappd.com/b/z/1"}),
        "u:2": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), last_in_result=True,
                       info={"kind": "menu", "brewery": "Zagovor", "logo": "https://assets.untappd.com/b.jpg", "url": "https://untappd.com/b/z/2"}),
        "u:3": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), last_in_result=True,
                       info={"kind": "menu", "brewery": "Konix", "logo": "https://assets.untappd.com/c.jpg", "url": "https://untappd.com/b/k/3"})}}
    state.beers["u:3"] = BeerRec(first_seen_city=iso(NOW), country="Armenia", rating_at=iso(NOW - timedelta(days=40)))
    keys = [key for key, _ in run_mod._country_candidates(state, NOW)]
    assert keys == ["u:1"]          # Konix is known, and one Zagovor beer is enough


def test_apply_untappd_country_by_beer_and_by_brewery_and_beats_the_shops_own():
    state = empty_state(NOW)
    state.pairs = {
        "gargoyle": {"u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "menu", "brewery": "Zagovor"}),
                     "u:2": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "menu", "brewery": "Zagovor"}),
                     "u:3": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "menu", "brewery": "Nobody"})},
        "beer-city": {"n:x": PairRec(first_seen=iso(NOW), last_seen=iso(NOW),
                                     info={"kind": "shop", "name": "X", "country": "Russia"}),
                      "n:y": PairRec(first_seen=iso(NOW), last_seen=iso(NOW),
                                     info={"kind": "shop", "name": "Y", "country": "Ukraine"})}}
    state.beers["u:1"] = BeerRec(first_seen_city=iso(NOW), country="Armenia")
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=1, matched_at=iso(NOW), via="local")
    run_mod.apply_untappd_country(state)
    assert state.pairs["gargoyle"]["u:1"].info["country"] == "Armenia"       # the beer's own page
    assert state.pairs["gargoyle"]["u:2"].info["country"] == "Armenia"       # same brewery, another beer
    assert "country" not in state.pairs["gargoyle"]["u:3"].info
    assert state.pairs["beer-city"]["n:x"].info["country"] == "Armenia"      # matched shop row: Untappd wins
    assert state.pairs["beer-city"]["n:y"].info["country"] == "Ukraine"      # unmatched: the shop's own stays


def test_apply_known_beer_info_fills_missing_style_abv_ibu_but_keeps_the_boards_own():
    state = empty_state(NOW)
    state.pairs = {"ferment": {
        "u:1715344": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "manual", "name": "Fruitage"}),
        "u:4713": PairRec(first_seen=iso(NOW), last_seen=iso(NOW),
                          info={"kind": "manual", "name": "Brugse Zot", "style": "Blonde", "abv": 6.0})}}
    state.beers["u:1715344"] = BeerRec(first_seen_city=iso(NOW), style="Fruit Beer", abv=3.4, ibu=7)
    state.beers["u:4713"] = BeerRec(first_seen_city=iso(NOW), style="Belgian Blonde", abv=6.2, ibu=20)
    run_mod.apply_known_beer_info(state)
    fruitage, zot = state.pairs["ferment"]["u:1715344"].info, state.pairs["ferment"]["u:4713"].info
    assert (fruitage["style"], fruitage["abv"], fruitage["ibu"]) == ("Fruit Beer", 3.4, 7)
    assert (zot["style"], zot["abv"], zot["ibu"]) == ("Blonde", 6.0, 20)      # the board's own wins, gaps filled


def test_apply_shop_matches_takes_a_manual_links_details_from_its_read_beer_page():
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(first_seen=iso(NOW), last_seen=iso(NOW),
                                               info={"kind": "shop", "name": "X", "abv": 4.9})}}
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=512, url="https://untappd.com/beer/512",
                                             matched_at=iso(NOW), via="manual")
    state.beers["u:512"] = BeerRec(first_seen_city=iso(NOW), name="Pils", brewery="Alpirsbacher", rating=3.4,
                                   style="Pilsner - German", abv=4.9, logo="https://assets.untappd.com/p.jpg")
    run_mod.apply_shop_matches(state)
    info = state.pairs["beer-city"]["n:x"].info
    assert (info["rating"], info["style"], info["logo"], info["u_name"]) == (3.4, "Pilsner - German",
                                                                          "https://assets.untappd.com/p.jpg", "Pils")


def test_apply_known_beer_info_uses_the_cached_label_of_a_beer_page():
    state = empty_state(NOW)
    state.pairs = {"ferment": {"u:1715344": _manual_pair(brewery="Rodenbach")}}
    state.beers["u:1715344"] = BeerRec(first_seen_city=iso(NOW), logo="https://assets.untappd.com/l.jpg")
    run_mod.apply_known_beer_info(state)
    assert state.pairs["ferment"]["u:1715344"].info["logo"] == "https://assets.untappd.com/l.jpg"


def test_apply_known_beer_info_fills_a_checkin_pair_never_resighted_since_its_page_was_read():
    """Bug A: a check-in pair's own sighting never carries style/rating; rules._backfill_checkin only
    fills it when the pair is re-sighted the same run. A beer whose page was read (state.beers filled)
    on a run where it was NOT re-sighted must still pick up the details -- not only manual boards."""
    state = empty_state(NOW)
    state.pairs = {"ferment": {"u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW),
                                              info={"kind": "checkin", "checkin_at": iso(NOW)})}}
    state.beers["u:1"] = BeerRec(first_seen_city=iso(NOW), style="Lager - Dark", rating=3.6, rating_at=iso(NOW))
    run_mod.apply_known_beer_info(state)
    info = state.pairs["ferment"]["u:1"].info
    assert (info["style"], info["rating"]) == ("Lager - Dark", 3.6)


def test_apply_known_beer_info_keeps_a_checkins_own_style_and_only_fills_the_missing_rating():
    """Never overwrite a value the check-in's own source (the check-in page) already provided."""
    state = empty_state(NOW)
    state.pairs = {"dors": {"u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW),
                                           info={"kind": "checkin", "checkin_at": iso(NOW),
                                                 "style": "Pale Ale - Other"})}}
    state.beers["u:1"] = BeerRec(first_seen_city=iso(NOW), style="should not overwrite", rating=4.1)
    run_mod.apply_known_beer_info(state)
    info = state.pairs["dors"]["u:1"].info
    assert (info["style"], info["rating"]) == ("Pale Ale - Other", 4.1)


def test_apply_known_beer_info_fills_a_checkin_pair_from_a_menu_pair_of_the_same_beer():
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW),
                                   info={"kind": "menu", "style": "Belgian Dubbel", "rating": 3.9})},
        "ferment": {"u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW),
                                   info={"kind": "checkin", "checkin_at": iso(NOW)})},
    }
    run_mod.apply_known_beer_info(state)
    info = state.pairs["ferment"]["u:1"].info
    assert (info["style"], info["rating"]) == ("Belgian Dubbel", 3.9)


def test_apply_known_beer_info_does_not_fill_a_menu_pair():
    """Only check-in and manual pairs are fill targets; a menu pair keeps reading its own source."""
    state = empty_state(NOW)
    state.pairs = {"beatles": {"u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "menu"})}}
    state.beers["u:1"] = BeerRec(first_seen_city=iso(NOW), style="X", rating=4.0)
    run_mod.apply_known_beer_info(state)
    assert "style" not in state.pairs["beatles"]["u:1"].info


def test_bar_beer_candidates_excludes_checkins_older_than_21_days():
    state = empty_state(NOW)
    state.pairs = {"t": {"u:1": _checkin_pair(iso(NOW - timedelta(days=22)))}}
    assert run_mod._bar_beer_candidates(state, _config(), NOW) == []


def test_bar_beer_candidates_retries_a_ten_day_old_read_still_missing_a_style():
    """Bug C: rating is cached but style is still missing, so the page counts as incomplete -- it gets
    the short BEER_RATING_RETRY_DAYS (3) retry window, not the full BEER_RATING_MAX_AGE_DAYS (30)
    reserved for a page that came back complete."""
    state = empty_state(NOW)
    state.pairs = {"t": {"u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1")}}
    state.beers["u:1"] = BeerRec(first_seen_city=iso(NOW), rating=4.0, rating_at=iso(NOW - timedelta(days=10)))
    assert run_mod._bar_beer_candidates(state, _config(), NOW) == [("u:1", "https://untappd.com/b/x/1")]


def test_bar_beer_candidates_does_not_retry_an_incomplete_read_within_3_days():
    state = empty_state(NOW)
    state.pairs = {"t": {"u:1": _checkin_pair(iso(NOW - timedelta(days=1)))}}
    state.beers["u:1"] = BeerRec(first_seen_city=iso(NOW), rating=4.0, rating_at=iso(NOW - timedelta(days=2)))
    assert run_mod._bar_beer_candidates(state, _config(), NOW) == []


def test_bar_beer_candidates_refetches_a_stale_cached_rating():
    state = empty_state(NOW)
    state.pairs = {"t": {"u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1")}}
    state.beers["u:1"] = BeerRec(first_seen_city=iso(NOW), rating=4.0, rating_at=iso(NOW - timedelta(days=31)))
    assert run_mod._bar_beer_candidates(state, _config(), NOW) == [("u:1", "https://untappd.com/b/x/1")]


def test_fetch_bar_beer_pages_caches_parsed_fields():
    state = empty_state(NOW)
    state.pairs = {"t": {"u:1559917": _checkin_pair(iso(NOW - timedelta(days=1)), url=BEER_URL)}}
    client = _untappd_client({BEER_URL: BEER_PAGE_HTML})
    run_mod.fetch_bar_beer_pages(state, _config(), client, NOW)
    beer = state.beers["u:1559917"]
    assert (beer.rating, beer.style, beer.abv, beer.ibu, beer.rating_at) == (3.82, "Fruit Beer", 6.2, 18, iso(NOW))


def test_fetch_bar_beer_pages_stops_on_budget_error_without_consuming_the_others():
    state = empty_state(NOW)
    state.pairs = {"t": {"u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/x/1"),
                        "u:2": _checkin_pair(iso(NOW - timedelta(days=2)), url="https://untappd.com/b/y/2")}}
    client = _untappd_client({}, daily_pages=0)   # no budget at all
    run_mod.fetch_bar_beer_pages(state, _config(), client, NOW)
    assert state.beers == {}


def test_fetch_bar_beer_pages_dumps_debug_html_on_a_page_that_does_not_parse(monkeypatch, tmp_path):
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("TAPS_DEBUG_DIR", str(debug_dir))
    state = empty_state(NOW)
    state.pairs = {"t": {"u:1559917": _checkin_pair(iso(NOW - timedelta(days=1)), url=BEER_URL)}}
    client = _untappd_client({BEER_URL: "<html><body>login wall</body></html>"})

    run_mod.fetch_bar_beer_pages(state, _config(), client, NOW)

    written = (debug_dir / "untappd_beer_1559917.html").read_text(encoding="utf-8")
    assert written.splitlines()[0] == f"<!-- {BEER_URL} -->"
    beer = state.beers["u:1559917"]
    assert beer.rating is None and beer.rating_at == iso(NOW)   # stamped, so it isn't retried every run


def test_fetch_bar_beer_pages_dumps_one_parsed_page_as_a_sample(monkeypatch, tmp_path):
    """One real beer page per run reaches the debug artifact even when it parses, so a parser (e.g. the
    label image) can be verified against production markup without spending extra pages."""
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("TAPS_DEBUG_DIR", str(debug_dir))
    state = empty_state(NOW)
    state.pairs = {"t": {"u:1559917": _checkin_pair(iso(NOW - timedelta(days=1)), url=BEER_URL)}}
    client = _untappd_client({BEER_URL: BEER_PAGE_HTML})

    run_mod.fetch_bar_beer_pages(state, _config(), client, NOW)

    assert (debug_dir / "untappd_beer_sample_1559917.html").exists()
    assert not (debug_dir / "untappd_beer_1559917.html").exists()   # that name is for pages that failed


def test_fetch_beer_pages_dumps_only_one_sample_across_tiers_per_run(monkeypatch, tmp_path):
    """The "one parsed sample page per run" dump is shared across tiers: `sampled` threads from the
    bar tier's call into the shop tier's, so a second parsed page in the same run isn't dumped too."""
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("TAPS_DEBUG_DIR", str(debug_dir))
    state = empty_state(NOW)
    state.pairs = {"t": {"u:1559917": _checkin_pair(iso(NOW - timedelta(days=1)), url=BEER_URL)}}
    client = _untappd_client({BEER_URL: BEER_PAGE_HTML, REAL_BEER_URL: REAL_BEER_PAGE})

    sampled = run_mod.fetch_bar_beer_pages(state, _config(), client, NOW)
    assert sampled is True
    run_mod._fetch_beer_pages(state, client, NOW, [("u:1715344", REAL_BEER_URL)], sampled)

    assert (debug_dir / "untappd_beer_sample_1559917.html").exists()
    assert not (debug_dir / "untappd_beer_sample_1715344.html").exists()   # already had a sample this run


def test_fetch_bar_beer_pages_survives_a_parse_crash_and_continues(monkeypatch):
    """I-1, unit-level: a parse error must fail only that beer -- it is stamped checked, and the loop
    continues to the next candidate instead of crashing the run."""
    state = empty_state(NOW)
    state.pairs = {"t": {
        "u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/a/1"),
        "u:2": _checkin_pair(iso(NOW - timedelta(days=2)), url="https://untappd.com/b/b/2"),
    }}
    client = _untappd_client({"https://untappd.com/b/a/1": "<html></html>", "https://untappd.com/b/b/2": "<html></html>"})
    monkeypatch.setattr(run_mod, "parse_beer_page", lambda html: (_ for _ in ()).throw(ValueError("boom")))

    run_mod.fetch_bar_beer_pages(state, _config(), client, NOW)

    assert state.beers["u:1"].rating_at == iso(NOW) and state.beers["u:2"].rating_at == iso(NOW)


# --- v1.1 §3: match shop beers to Untappd via search -------------------------

def _shop_pair(brand, name, last_seen=None, **extra):
    return PairRec(first_seen=iso(NOW), last_seen=last_seen or iso(NOW),
                   info={"kind": "shop", "source": "parma", "brewery": brand, "name": name, **extra})


KILIKIA_RESULT_HTML = """
<div class="beer-item" data-bid="1547626">
 <a class="label" href="/b/kilikia-brewery-kilikia/1547626">
  <img src="https://assets.untappd.com/site/beer_logos/beer-1547626.jpeg"></a>
 <div class="beer-details">
  <p class="name"><a href="/b/kilikia-brewery-kilikia/1547626">Kilikia</a></p>
  <p class="brewery">Kilikia Brewery</p>
  <p class="style">Pale Lager</p>
 </div>
 <div class="details beer">
  <div class="abv">4.6% ABV</div>
  <div class="caps" data-rating="3.21"></div>
 </div>
</div>
"""
KILIKIA_SEARCH_URL = "https://untappd.com/search?q=Kilikia&type=beer"   # v1.3: brand not duplicated, name already is it


def test_shop_match_candidates_excludes_already_matched_and_untappd_keyed():
    state = empty_state(NOW)
    state.pairs = {
        "parma": {
            "n:kilikia": _shop_pair("Kilikia", "Kilikia"),
            "u:1": _shop_pair("Dahook", "Hell"),   # already an Untappd id (e.g. via alias): no search
        },
    }
    state.shop_matches["n:kilikia"] = ShopMatchRec(untappd_beer_id=1, matched_at=iso(NOW))
    assert run_mod._shop_match_candidates(state, NOW) == []


def test_shop_match_candidates_prioritizes_never_searched_over_a_stale_no_match_retry():
    """v1.3 (по приоритетам): a never-searched beer comes before a retry of an old "no_match", even
    when the retry was seen more recently."""
    state = empty_state(NOW)
    state.pairs = {"parma": {
        "n:kilikia": _shop_pair("Kilikia", "Kilikia", last_seen=iso(NOW)),           # retry, seen more recently
        "n:a": _shop_pair("Brand", "A", last_seen=iso(NOW - timedelta(days=1))),      # never searched
    }}
    state.shop_matches["n:kilikia"] = ShopMatchRec(matched_at=iso(NOW - timedelta(days=31)))
    keys = [key for key, *_ in run_mod._shop_match_candidates(state, NOW)]
    assert keys == ["n:a", "n:kilikia"]


def test_shop_match_candidates_retries_a_stale_no_match():
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    state.shop_matches["n:kilikia"] = ShopMatchRec(matched_at=iso(NOW - timedelta(days=31)))
    assert run_mod._shop_match_candidates(state, NOW) == [("n:kilikia", "Kilikia", "Kilikia", None)]


def test_shop_match_candidates_skips_a_fresh_no_match():
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    state.shop_matches["n:kilikia"] = ShopMatchRec(
        matched_at=iso(NOW - timedelta(days=29)), search_v=run_mod.SEARCH_LOGIC_VERSION)
    assert run_mod._shop_match_candidates(state, NOW) == []


def test_shop_match_candidates_retries_a_fresh_no_match_from_an_older_search_version():
    """v1.3 search fix: a "no_match" produced by an older, worse query/acceptance version is
    retried right away -- once -- instead of waiting out the usual 30 days."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    state.shop_matches["n:kilikia"] = ShopMatchRec(
        matched_at=iso(NOW - timedelta(days=1)), search_v=run_mod.SEARCH_LOGIC_VERSION - 1)
    assert run_mod._shop_match_candidates(state, NOW) == [("n:kilikia", "Kilikia", "Kilikia", None)]


def test_shop_match_candidates_skips_a_fresh_no_match_already_at_the_current_search_version():
    """Regression guard: once a "no_match" has been retried under the current version, the usual
    30-day wait applies again -- it is not retried on every run forever."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    state.shop_matches["n:kilikia"] = ShopMatchRec(
        matched_at=iso(NOW - timedelta(days=1)), search_v=run_mod.SEARCH_LOGIC_VERSION)
    assert run_mod._shop_match_candidates(state, NOW) == []


def test_shop_match_candidates_does_not_retry_a_manual_block_via_an_older_search_version():
    """A corrections.yaml same_as untappd_id: null block ("не то же") must never be retried by
    search, whether via the usual 30-day wait or the search-version bypass."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    state.shop_matches["n:kilikia"] = ShopMatchRec(
        via="manual", matched_at=iso(NOW), search_v=run_mod.SEARCH_LOGIC_VERSION - 1)
    assert run_mod._shop_match_candidates(state, NOW) == []


def test_shop_match_candidates_ignores_a_fresh_search_no_match_when_limit_is_none():
    """Local matching costs zero Untappd pages, so a fresh search "no_match" record must not block
    it -- only match_shop_beers_locally ever calls with limit=None (v1.2 beer identity)."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    state.shop_matches["n:kilikia"] = ShopMatchRec(matched_at=iso(NOW - timedelta(days=1)))
    assert run_mod._shop_match_candidates(state, NOW, limit=None) == [("n:kilikia", "Kilikia", "Kilikia", None)]


def test_shop_match_candidates_still_skips_a_manual_block_when_limit_is_none():
    """Unlike a search no_match, a corrections.yaml same_as untappd_id: null override ("не то же",
    via="manual") must keep blocking local matching too."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    state.shop_matches["n:kilikia"] = ShopMatchRec(via="manual", matched_at=iso(NOW))
    assert run_mod._shop_match_candidates(state, NOW, limit=None) == []


def test_shop_match_candidates_skips_pairs_without_brand_or_name():
    state = empty_state(NOW)
    state.pairs = {"parma": {
        "n:a": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "shop", "name": "A"}),
        "n:b": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "shop", "brewery": "B"}),
    }}
    assert run_mod._shop_match_candidates(state, NOW) == []


def _u_pair(name, brewery, last_seen=None, **extra):
    return PairRec(first_seen=iso(NOW), last_seen=last_seen or iso(NOW),
                   info={"kind": "menu", "source": "untappd_menu", "name": name, "brewery": brewery, **extra})


def test_known_untappd_beers_from_pairs_deduped_and_requires_name_and_brewery():
    """v1.2 beer identity: every "u:"-keyed pair across all places/bars is a known Untappd beer for
    local matching, deduped by id; a pair missing name or brewery (incomplete data) is skipped."""
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:1674726": _u_pair("Apricot Ale (Prunus Armeniaca)", "Dargett Brewery")},
        "gargoyle": {
            "u:1674726": _u_pair("Apricot Ale (Prunus Armeniaca)", "Dargett Brewery"),   # same beer, another bar
            "u:2": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "menu", "name": "No Brewery"}),
            "n:kilikia": _shop_pair("Kilikia", "Kilikia"),   # not an Untappd-native key
        },
    }
    assert run_mod.known_untappd_beers(state) == [
        KnownBeer(untappd_id=1674726, name="Apricot Ale (Prunus Armeniaca)", brewery="Dargett Brewery"),
    ]


def test_apply_same_as_records_a_manual_match_with_known_name_and_brewery():
    """v1.2 beer identity: corrections.yaml same_as, applied with the Untappd beer's own name/
    brewery when already known from a bar's menu (e.g. Chimay, too different for local matching)."""
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:34039": _u_pair("Chimay Grande Réserve (Blue)", "Bières de Chimay")},
        "beer-city": {"n:chimay peres trappistes blue": _shop_pair("Chimay", "Chimay peres trappistes blue")},
    }
    corrections = run_mod.Corrections(same_as={("beer-city", "n:chimay peres trappistes blue"): 34039})
    run_mod.apply_same_as(state, corrections, NOW)
    match = state.shop_matches["n:chimay peres trappistes blue"]
    assert (match.untappd_beer_id, match.via, match.name, match.brewery) == (
        34039, "manual", "Chimay Grande Réserve (Blue)", "Bières de Chimay")
    assert match.url == "https://untappd.com/beer/34039"
    assert match.matched_at == iso(NOW)


def test_apply_same_as_works_even_when_the_beer_is_not_otherwise_known():
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": _shop_pair("X", "X")}}
    corrections = run_mod.Corrections(same_as={("beer-city", "n:x"): 12345})
    run_mod.apply_same_as(state, corrections, NOW)
    match = state.shop_matches["n:x"]
    assert (match.untappd_beer_id, match.via, match.name, match.brewery) == (12345, "manual", None, None)


def test_apply_same_as_uses_the_entrys_own_name_and_brewery_for_an_unknown_beer():
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": _shop_pair("X", "X")}}
    corrections = run_mod.Corrections(same_as={("beer-city", "n:x"): 12345},
                                      same_as_names={("beer-city", "n:x"): ("Trehgornoe Blanche", "Moscow Brewing Company")})
    run_mod.apply_same_as(state, corrections, NOW)
    match = state.shop_matches["n:x"]
    assert (match.name, match.brewery) == ("Trehgornoe Blanche", "Moscow Brewing Company")


def test_apply_same_as_wins_over_an_existing_local_or_search_match():
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": _shop_pair("X", "X")}}
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=999, via="search", matched_at=iso(NOW))
    corrections = run_mod.Corrections(same_as={("beer-city", "n:x"): 12345})
    run_mod.apply_same_as(state, corrections, NOW)
    assert state.shop_matches["n:x"].untappd_beer_id == 12345
    assert state.shop_matches["n:x"].via == "manual"


def test_apply_same_as_drops_a_manual_match_removed_from_corrections():
    """Each run: a same_as entry the owner deleted from corrections.yaml must not leave a stale
    manual override behind -- local/search matching should resume for that key (code review)."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": _shop_pair("X", "X")}}
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=12345, via="manual", matched_at=iso(NOW - timedelta(days=1)))
    run_mod.apply_same_as(state, run_mod.Corrections(), NOW)   # no same_as entries any more
    assert "n:x" not in state.shop_matches


def test_apply_same_as_leaves_non_manual_matches_alone_when_dropping_stale():
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": _shop_pair("X", "X")}}
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=999, via="local", matched_at=iso(NOW))
    run_mod.apply_same_as(state, run_mod.Corrections(), NOW)
    assert state.shop_matches["n:x"].via == "local"   # same_as only ever drops its own via="manual"


def test_apply_same_as_null_untappd_id_blocks_matching_without_claiming_an_id():
    """corrections.yaml same_as untappd_id: null -- "не то же" -- records a manual block (v1.2 beer
    identity) without a real Untappd id or url."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": _shop_pair("X", "X")}}
    corrections = run_mod.Corrections(same_as={("beer-city", "n:x"): None})
    run_mod.apply_same_as(state, corrections, NOW)
    match = state.shop_matches["n:x"]
    assert (match.untappd_beer_id, match.via, match.url) == (None, "manual", None)
    assert match.matched_at == iso(NOW)


def test_apply_same_as_null_override_keeps_blocking_local_matching_every_run():
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": _shop_pair("X", "X")}}
    corrections = run_mod.Corrections(same_as={("beer-city", "n:x"): None})
    run_mod.apply_same_as(state, corrections, NOW)
    assert run_mod._shop_match_candidates(state, NOW, limit=None) == []


def test_match_shop_beers_locally_records_a_local_match():
    """v1.2 beer identity: before any Untappd search, a shop beer already known from a bar's own
    menu is matched for free and marked via="local", with the Untappd beer's own name/brewery."""
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:1674726": _u_pair("Apricot Ale (Prunus Armeniaca)", "Dargett Brewery")},
        "beer-city": {"n:dargett apricot ale": _shop_pair("Dargett", "Dargett apricot ale")},
    }
    run_mod.match_shop_beers_locally(state, NOW)
    match = state.shop_matches["n:dargett apricot ale"]
    assert (match.untappd_beer_id, match.via, match.name, match.brewery) == (
        1674726, "local", "Apricot Ale (Prunus Armeniaca)", "Dargett Brewery")
    assert match.url == "https://untappd.com/beer/1674726"
    assert match.matched_at == iso(NOW)


def test_match_shop_beers_locally_copies_rating_style_abv_logo_and_sets_checked_at():
    """A local match is already a known, previously-fetched Untappd beer -- copy its cached display
    fields too, and stamp checked_at so refresh_shop_matches doesn't spend a page re-fetching it."""
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:1674726": _u_pair("Apricot Ale (Prunus Armeniaca)", "Dargett Brewery",
                                         rating=4.02, style="Fruit Beer", abv=5.5, logo="https://x/logo.jpg")},
        "beer-city": {"n:dargett apricot ale": _shop_pair("Dargett", "Dargett apricot ale")},
    }
    run_mod.match_shop_beers_locally(state, NOW)
    match = state.shop_matches["n:dargett apricot ale"]
    assert (match.rating, match.style, match.abv, match.logo) == (4.02, "Fruit Beer", 5.5, "https://x/logo.jpg")
    assert match.checked_at == iso(NOW)


def test_match_shop_beers_locally_rejects_a_parenthetical_match_when_abv_differs_a_lot():
    """Code review round 2, finding C: the shop's own scraped abv is threaded through to
    local_match, so a loose match that only works via a parenthesised aside in the candidate's
    Untappd name does not paper over a real difference in strength."""
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:1": _u_pair("Sour (Raspberry)", "Dargett Brewery", abv=6.5)},
        "beer-city": {"n:dargett sour": _shop_pair("Dargett", "Dargett Sour", abv=4.0)},
    }
    run_mod.match_shop_beers_locally(state, NOW)
    assert state.shop_matches == {}


def test_match_shop_beers_locally_marks_an_uncorroborated_parenthetical_match_weak():
    """Code review round 3, finding C2 (decision: do not tighten the rule further): a loose match
    that only works via an unnamed parenthesised aside, with no ABV on either side to corroborate
    it, is still recorded -- but flagged weak=True so a review page can list it first."""
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:1": _u_pair("Sour (Raspberry)", "Dargett Brewery")},
        "beer-city": {"n:dargett sour": _shop_pair("Dargett", "Dargett Sour")},
    }
    run_mod.match_shop_beers_locally(state, NOW)
    assert state.shop_matches["n:dargett sour"].weak is True


def test_match_shop_beers_locally_does_not_mark_an_ordinary_match_weak():
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:1": _u_pair("Oatmeal Stout", "Dargett Brewery")},
        "beer-city": {"n:dargett oatmeal stout": _shop_pair("Dargett", "Dargett Oatmeal Stout")},
    }
    run_mod.match_shop_beers_locally(state, NOW)
    assert state.shop_matches["n:dargett oatmeal stout"].weak is False


def test_match_shop_beers_locally_leaves_no_match_when_nothing_qualifies():
    """No caching of a local miss (unlike search's no_match): it's free to retry every run."""
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:1": _u_pair("Hell", "Dahook")},
        "parma": {"n:x": _shop_pair("Nonexistent Brand", "Nonexistent Beer")},
    }
    run_mod.match_shop_beers_locally(state, NOW)
    assert state.shop_matches == {}


def test_match_shop_beers_locally_skips_already_matched_keys():
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:1674726": _u_pair("Apricot Ale (Prunus Armeniaca)", "Dargett Brewery")},
        "beer-city": {"n:dargett apricot ale": _shop_pair("Dargett", "Dargett apricot ale")},
    }
    state.shop_matches["n:dargett apricot ale"] = ShopMatchRec(untappd_beer_id=999, via="search", matched_at=iso(NOW))
    run_mod.match_shop_beers_locally(state, NOW)
    assert state.shop_matches["n:dargett apricot ale"].untappd_beer_id == 999   # untouched


def test_shop_match_candidates_includes_buyam_menu_and_manual_kinds():
    """v1.2 beer identity: Dargett Brewpub (buyam, kind "menu") and a friend's sighting (kind
    "manual") are candidates too, not only shop kind -- an Untappd-native "u:" menu pair still is not."""
    state = empty_state(NOW)
    state.pairs = {
        "dargett-brewpub": {"n:apricot ale": PairRec(
            first_seen=iso(NOW), last_seen=iso(NOW),
            info={"kind": "menu", "source": "buyam", "brewery": "Dargett", "name": "Apricot Ale"})},
        "gargoyle": {"u:1": PairRec(
            first_seen=iso(NOW), last_seen=iso(NOW),
            info={"kind": "menu", "source": "untappd_menu", "brewery": "X", "name": "Y"})},
        "tap-station": {"n:hazy pale": PairRec(
            first_seen=iso(NOW), last_seen=iso(NOW),
            info={"kind": "manual", "source": "manual", "brewery": "379", "name": "Hazy Pale"})},
    }
    candidates = {key for key, _, _, _ in run_mod._shop_match_candidates(state, NOW)}
    assert candidates == {"n:apricot ale", "n:hazy pale"}


def test_shop_match_candidates_most_recently_seen_first_capped_at_eight():
    state = empty_state(NOW)
    state.pairs = {"parma": {
        f"n:beer{i}": _shop_pair("Brand", f"Beer {i}", last_seen=iso(NOW - timedelta(days=i)))
        for i in range(1, 11)
    }}
    candidates = run_mod._shop_match_candidates(state, NOW)
    assert [key for key, _, _, _ in candidates] == [f"n:beer{i}" for i in range(1, 9)]


def test_shop_match_candidates_respects_a_custom_limit():
    """Owner's temporary Untappd boost (v1.1 boost_search_per_run): the cap is a parameter, not
    only the SHOP_SEARCH_CANDIDATES_PER_RUN default."""
    state = empty_state(NOW)
    state.pairs = {"parma": {
        f"n:beer{i}": _shop_pair("Brand", f"Beer {i}", last_seen=iso(NOW - timedelta(days=i)))
        for i in range(1, 11)
    }}
    candidates = run_mod._shop_match_candidates(state, NOW, limit=3)
    assert [key for key, _, _, _ in candidates] == ["n:beer1", "n:beer2", "n:beer3"]


def test_match_shop_beers_records_a_match():
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    client = _untappd_client({KILIKIA_SEARCH_URL: KILIKIA_RESULT_HTML})
    run_mod.match_shop_beers(state, client, NOW)
    match = state.shop_matches["n:kilikia"]
    assert (match.untappd_beer_id, match.url, match.rating, match.style, match.abv) == (
        1547626, "https://untappd.com/b/kilikia-brewery-kilikia/1547626", 3.21, "Pale Lager", 4.6)
    assert match.logo == "https://assets.untappd.com/site/beer_logos/beer-1547626.jpeg"
    assert match.matched_at == iso(NOW) and match.checked_at == iso(NOW)


def test_match_shop_beers_records_via_and_canonical_name_brewery():
    """v1.2 beer identity: a search match is tagged via="search" and carries the Untappd beer's own
    name/brewery too, exactly like a local match does."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    client = _untappd_client({KILIKIA_SEARCH_URL: KILIKIA_RESULT_HTML})
    run_mod.match_shop_beers(state, client, NOW)
    match = state.shop_matches["n:kilikia"]
    assert (match.via, match.name, match.brewery) == ("search", "Kilikia", "Kilikia Brewery")


def test_match_shop_beers_cleans_noise_from_the_query():
    """v1.2 beer identity: the same noise-cleaning as local matching applies to the search query
    string, e.g. "379 Dunkel dark" -- the shop's own colour suffix -- must not reach Untappd as-is."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:379 dunkel dark": _shop_pair("379", "379 Dunkel dark")}}
    client = _untappd_client({"https://untappd.com/search?q=379%20Dunkel&type=beer":
                              "<html><body>Nothing found.</body></html>"})
    run_mod.match_shop_beers(state, client, NOW)
    assert state.shop_matches["n:379 dunkel dark"].matched_at == iso(NOW)   # the cleaned URL was fetched


def test_match_shop_beers_searches_a_russian_name_without_the_latin_brewery():
    """Yerevan City shows "Жигулевское светлое" under the brewery "Jigulyovskoye": the search query is the
    Russian name alone and a Cyrillic Untappd result is accepted by name, not by brewery."""
    html = KILIKIA_RESULT_HTML.replace("Kilikia Brewery", "Очаково").replace(">Kilikia</a>", ">Жигулёвское</a>")
    state = empty_state(NOW)
    state.pairs = {"yerevan-city": {"n:jigulyovskoye light": _shop_pair("Jigulyovskoye", "Жигулевское светлое")}}
    client = _untappd_client({"https://untappd.com/search?q=%D0%96%D0%B8%D0%B3%D1%83%D0%BB%D0%B5%D0%B2%D1%81"
                              "%D0%BA%D0%BE%D0%B5%20%D1%81%D0%B2%D0%B5%D1%82%D0%BB%D0%BE%D0%B5&type=beer": html})
    run_mod.match_shop_beers(state, client, NOW)
    match = state.shop_matches["n:jigulyovskoye light"]
    assert (match.untappd_beer_id, match.name, match.brewery) == (1547626, "Жигулёвское", "Очаково")


def test_match_shop_beers_records_no_match_when_nothing_scores():
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:x": _shop_pair("Nonexistent Brand", "Nonexistent Item")}}
    client = _untappd_client({"https://untappd.com/search?q=Nonexistent%20Brand%20Nonexistent%20Item&type=beer":
                              KILIKIA_RESULT_HTML})
    run_mod.match_shop_beers(state, client, NOW)
    match = state.shop_matches["n:x"]
    assert (match.untappd_beer_id, match.url, match.rating) == (None, None, None)
    assert match.matched_at == iso(NOW)


def test_match_shop_beers_stores_the_best_rejected_candidate_as_a_suggestion():
    """v1.4 owner suggestion: a search page returned a result, but it did not pass matches() --
    stored on the failed ShopMatchRec for a one-tap "Возможно: ..." on the to-do list."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:x": _shop_pair("Nonexistent Brand", "Nonexistent Item")}}
    client = _untappd_client({"https://untappd.com/search?q=Nonexistent%20Brand%20Nonexistent%20Item&type=beer":
                              KILIKIA_RESULT_HTML})
    run_mod.match_shop_beers(state, client, NOW)
    match = state.shop_matches["n:x"]
    assert (match.suggest_id, match.suggest_name, match.suggest_brewery) == (1547626, "Kilikia", "Kilikia Brewery")


def test_match_shop_beers_leaves_no_suggestion_when_the_search_finds_nothing_at_all():
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    client = _untappd_client({KILIKIA_SEARCH_URL: "<html><body>Nothing found.</body></html>"})
    run_mod.match_shop_beers(state, client, NOW)
    match = state.shop_matches["n:kilikia"]
    assert (match.suggest_id, match.suggest_name, match.suggest_brewery) == (None, None, None)


def test_match_shop_beers_clears_a_stale_suggestion_once_a_real_match_is_made():
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    state.shop_matches["n:kilikia"] = ShopMatchRec(matched_at=iso(NOW - timedelta(days=40)),
                                                   suggest_id=1, suggest_name="Old", suggest_brewery="Old Br")
    client = _untappd_client({KILIKIA_SEARCH_URL: KILIKIA_RESULT_HTML})
    run_mod.match_shop_beers(state, client, NOW)
    match = state.shop_matches["n:kilikia"]
    assert match.untappd_beer_id == 1547626
    assert (match.suggest_id, match.suggest_name, match.suggest_brewery) == (None, None, None)


def test_match_shop_beers_dumps_debug_html_on_empty_results(monkeypatch, tmp_path):
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("TAPS_DEBUG_DIR", str(debug_dir))
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    client = _untappd_client({KILIKIA_SEARCH_URL: "<html><body>Nothing found.</body></html>"})

    run_mod.match_shop_beers(state, client, NOW)

    written = (debug_dir / "untappd_search_n_kilikia.html").read_text(encoding="utf-8")
    assert written.splitlines()[0] == f"<!-- {KILIKIA_SEARCH_URL} -->"
    assert state.shop_matches["n:kilikia"].untappd_beer_id is None


def test_match_shop_beers_stops_on_budget_error_without_consuming_the_others():
    state = empty_state(NOW)
    state.pairs = {"parma": {
        "n:a": _shop_pair("A", "A", last_seen=iso(NOW - timedelta(days=1))),
        "n:b": _shop_pair("B", "B", last_seen=iso(NOW - timedelta(days=2))),
    }}
    client = _untappd_client({}, daily_pages=0)
    run_mod.match_shop_beers(state, client, NOW)
    assert state.shop_matches == {}


def test_match_shop_beers_tries_a_simpler_fallback_query_when_the_first_finds_nothing():
    """v1.3 search fix: budget of (at most) 2 pages per beer -- a second, simpler query (the brand
    alone) is tried once the first (full) query comes back with no acceptable result."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:x": _shop_pair("Ayinger", "Ayinger Celebrator Doppelbock")}}
    fallback_html = (KILIKIA_RESULT_HTML.replace("Kilikia Brewery", "Ayinger Privatbrauerei")
                     .replace(">Kilikia</a>", ">Celebrator Doppelbock</a>"))
    client = _untappd_client({
        "https://untappd.com/search?q=Ayinger%20Celebrator%20Doppelbock&type=beer":
            "<html><body>Nothing found.</body></html>",
        "https://untappd.com/search?q=Ayinger&type=beer": fallback_html,
    })
    run_mod.match_shop_beers(state, client, NOW)
    match = state.shop_matches["n:x"]
    assert (match.untappd_beer_id, match.via, match.name, match.brewery) == (
        1547626, "search", "Celebrator Doppelbock", "Ayinger Privatbrauerei")


def test_match_shop_beers_does_not_repeat_an_identical_fallback_query():
    """"Kilikia"/"Kilikia" already cleans to the brand alone -- a fallback would just refetch the
    same query, so only one page is spent."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    client = _untappd_client({KILIKIA_SEARCH_URL: "<html><body>Nothing found.</body></html>"})
    run_mod.match_shop_beers(state, client, NOW)
    assert client.untappd.pages_today == 1
    assert state.shop_matches["n:kilikia"].untappd_beer_id is None


def test_match_shop_beers_respects_a_custom_limit():
    state = empty_state(NOW)
    state.pairs = {"parma": {
        "n:a": _shop_pair("A", "A", last_seen=iso(NOW - timedelta(days=1))),
        "n:b": _shop_pair("B", "B", last_seen=iso(NOW - timedelta(days=2))),
    }}
    client = _untappd_client({
        "https://untappd.com/search?q=A&type=beer": "<html><body>Nothing found.</body></html>",
        "https://untappd.com/search?q=B&type=beer": "<html><body>Nothing found.</body></html>",
    })
    run_mod.match_shop_beers(state, client, NOW, limit=1)
    assert list(state.shop_matches) == ["n:a"]                 # only the most recently seen one


def test_match_shop_beers_stays_within_the_page_budget_across_fallback_queries():
    """v1.3 search fix: `limit` counts PAGES, not beers -- once every beer needs its full 2-page
    budget (first + fallback), fewer beers get processed, but total pages fetched never exceeds
    `limit`."""
    state = empty_state(NOW)
    state.pairs = {"parma": {
        f"n:beer{i}": _shop_pair(f"Brand{i}", f"Brand{i} Beer{i}", last_seen=iso(NOW - timedelta(days=i)))
        for i in range(1, 4)
    }}
    client = _untappd_client({
        url: "<html><body>Nothing found.</body></html>"
        for i in range(1, 4)
        for url in (f"https://untappd.com/search?q=Brand{i}%20Beer{i}&type=beer",
                    f"https://untappd.com/search?q=Brand{i}&type=beer")
    })
    run_mod.match_shop_beers(state, client, NOW, limit=3)
    assert client.untappd.pages_today == 3                     # never exceeds the page budget...
    assert set(state.shop_matches) == {"n:beer1", "n:beer2"}   # ...even though beer3 never got a page


def test_match_shop_beers_prefers_a_result_whose_abv_agrees_when_several_pass():
    """v1.3 search fix: ABV as extra evidence to disambiguate when more than one result otherwise
    passes matches() -- the agreeing one wins even when listed second."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:x": _shop_pair("Delirium", "Delirium Tremens", abv=8.5)}}
    two_results = """
<div class="beer-item" data-bid="1">
 <a class="label" href="/b/delirium-tremens-nitro/1"><img src="https://x/1.jpg"></a>
 <div class="beer-details">
  <p class="name"><a href="/b/delirium-tremens-nitro/1">Tremens Nitro</a></p>
  <p class="brewery">Huyghe / Delirium</p>
  <p class="style">Strong Pale Ale</p>
 </div>
 <div class="details beer"><div class="abv">5.0% ABV</div><div class="caps" data-rating="3.5"></div></div>
</div>
<div class="beer-item" data-bid="2">
 <a class="label" href="/b/delirium-tremens/2"><img src="https://x/2.jpg"></a>
 <div class="beer-details">
  <p class="name"><a href="/b/delirium-tremens/2">Tremens</a></p>
  <p class="brewery">Huyghe / Delirium</p>
  <p class="style">Strong Pale Ale</p>
 </div>
 <div class="details beer"><div class="abv">8.5% ABV</div><div class="caps" data-rating="4.1"></div></div>
</div>
"""
    client = _untappd_client({"https://untappd.com/search?q=Delirium%20Tremens&type=beer": two_results})
    run_mod.match_shop_beers(state, client, NOW)
    match = state.shop_matches["n:x"]
    assert (match.untappd_beer_id, match.abv) == (2, 8.5)       # not 1 (5.0%), despite being listed first


def test_match_shop_beers_falls_back_to_the_first_passing_result_without_a_known_abv():
    """Regression guard: when the shop's own ABV is unknown, the existing "first match wins"
    tie-break is unchanged -- ABV is only ever extra evidence, never required."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    client = _untappd_client({KILIKIA_SEARCH_URL: KILIKIA_RESULT_HTML})
    run_mod.match_shop_beers(state, client, NOW)
    assert state.shop_matches["n:kilikia"].untappd_beer_id == 1547626


def test_match_shop_beers_stamps_the_current_search_version_on_a_new_no_match():
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:x": _shop_pair("Nonexistent Brand", "Nonexistent Item")}}
    client = _untappd_client({"https://untappd.com/search?q=Nonexistent%20Brand%20Nonexistent%20Item&type=beer":
                              "<html><body>Nothing found.</body></html>"})
    run_mod.match_shop_beers(state, client, NOW)
    assert state.shop_matches["n:x"].search_v == run_mod.SEARCH_LOGIC_VERSION


def test_match_shop_beers_stamps_the_current_search_version_on_a_new_match():
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    client = _untappd_client({KILIKIA_SEARCH_URL: KILIKIA_RESULT_HTML})
    run_mod.match_shop_beers(state, client, NOW)
    assert state.shop_matches["n:kilikia"].search_v == run_mod.SEARCH_LOGIC_VERSION


# --- v1.3 search fix: real production search-failure dumps (Cloudflare blocks this dev machine, so
# these are captured, anonymized real Untappd search pages -- see docs/superpowers -- not synthetic
# markup). Untappd's own Algolia widget reports "0 drink results" on every one of them: the shop's
# raw query (brand duplicated, plus noise) found nothing. Each below is a distinct failure pattern. --

REAL_DEDUP_HTML = fixture_text("untappd/search_dedup_ayinger.html")
REAL_UNFILTERED_HTML = fixture_text("untappd/search_unfiltered_dargett.html")
REAL_PARTIAL_NOISE_HTML = fixture_text("untappd/search_partial_noise_delirium.html")
REAL_LONG_NAME_HTML = fixture_text("untappd/search_long_name_gletcher.html")


def test_match_shop_beers_real_dump_plain_brand_duplication():
    """Real dump 1/4: "Ayinger" + "Ayinger celebrator dunkles" -- production sent the brand twice
    ("Ayinger Ayinger celebrator dunkles"); Untappd's Algolia widget found 0 results for it. The
    fixed query drops the duplicate ("Ayinger celebrator dunkles"), still finds nothing on this real
    capture, and the brand-alone fallback is tried within budget before giving up."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:x": _shop_pair("Ayinger", "Ayinger celebrator dunkles")}}
    client = _untappd_client({
        "https://untappd.com/search?q=Ayinger%20celebrator%20dunkles&type=beer": REAL_DEDUP_HTML,
        "https://untappd.com/search?q=Ayinger&type=beer": REAL_DEDUP_HTML,
    })
    run_mod.match_shop_beers(state, client, NOW)
    assert client.untappd.pages_today == 2                                   # both pages of the budget used
    assert state.shop_matches["n:x"] == ShopMatchRec(matched_at=iso(NOW), search_v=run_mod.SEARCH_LOGIC_VERSION)


def test_match_shop_beers_real_dump_unfiltered_noise_collapses_to_brand_alone():
    """Real dump 2/4: "Dargett" + "Dargett non-filtered" -- production sent "Dargett Dargett
    non-filtered"; the fixed query cleans down to just "Dargett" (dedup + noise-word), which already
    equals its own fallback, so only ONE page is spent on this real capture, not two."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:x": _shop_pair("Dargett", "Dargett non-filtered")}}
    client = _untappd_client({"https://untappd.com/search?q=Dargett&type=beer": REAL_UNFILTERED_HTML})
    run_mod.match_shop_beers(state, client, NOW)
    assert client.untappd.pages_today == 1                                   # no wasted identical fallback
    assert state.shop_matches["n:x"] == ShopMatchRec(matched_at=iso(NOW), search_v=run_mod.SEARCH_LOGIC_VERSION)


def test_match_shop_beers_real_dump_partial_clean_residual_descriptor_noise():
    """Real dump 3/4: "Delirium" + "Delirium tremens strong and blond" -- a translated shop
    descriptor ("strong and blond") that isn't part of Untappd's own beer name; dedup shortens the
    query, but "strong"/"blond" are not (yet) recognised noise, so the cleaned query still finds
    nothing on this real capture and the brand-alone fallback is spent too."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:x": _shop_pair("Delirium", "Delirium tremens strong and blond")}}
    client = _untappd_client({
        "https://untappd.com/search?q=Delirium%20tremens%20strong%20and%20blond&type=beer": REAL_PARTIAL_NOISE_HTML,
        "https://untappd.com/search?q=Delirium&type=beer": REAL_PARTIAL_NOISE_HTML,
    })
    run_mod.match_shop_beers(state, client, NOW)
    assert client.untappd.pages_today == 2
    assert state.shop_matches["n:x"] == ShopMatchRec(matched_at=iso(NOW), search_v=run_mod.SEARCH_LOGIC_VERSION)


def test_match_shop_beers_real_dump_long_multi_word_name():
    """Real dump 4/4: "Gletcher" + "Gletcher Milk Of Amnesia with mango and passion" -- dedup removes
    the repeated brand, but the name itself stays a long, specific 7-word phrase; the brand-alone
    fallback is the safety net for exactly this kind of over-long query on this real capture."""
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:x": _shop_pair("Gletcher", "Gletcher Milk Of Amnesia with mango and passion")}}
    client = _untappd_client({
        "https://untappd.com/search?q=Gletcher%20Milk%20Of%20Amnesia%20with%20mango%20and%20passion&type=beer":
            REAL_LONG_NAME_HTML,
        "https://untappd.com/search?q=Gletcher&type=beer": REAL_LONG_NAME_HTML,
    })
    run_mod.match_shop_beers(state, client, NOW)
    assert client.untappd.pages_today == 2
    assert state.shop_matches["n:x"] == ShopMatchRec(matched_at=iso(NOW), search_v=run_mod.SEARCH_LOGIC_VERSION)


# --- v1.3 owner priority: bars before shops before countries ----------------

def test_collect_untappd_fetches_tiers_in_priority_order():
    """Bar beer pages, then shop beer pages, then shop search, then country beer pages."""
    state = empty_state(NOW)
    state.pairs = {
        "gargoyle": {
            "u:1": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/bar/1"),
            "u:3": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), last_in_result=True,
                          info={"kind": "menu", "brewery": "Zagovor", "url": "https://untappd.com/b/zagovor/3"}),
        },
        "houl": {"u:2": _checkin_pair(iso(NOW - timedelta(days=1)), url="https://untappd.com/b/shop/2")},
        "beer-city": {"n:x": _shop_pair("Brand", "Name")},
    }
    config = Config(places={
        "gargoyle": Place(id="gargoyle", name="Gargoyle", kind="bar", sources={}),
        "houl": Place(id="houl", name="Houl", kind="shop", sources={}),
        "beer-city": Place(id="beer-city", name="Beer City", kind="shop", sources={}),
    }, breweries=(), settings=Settings())
    urls = []

    def fetch_page(url):
        urls.append(url)
        return HttpResponse(200, {}, "<html><body>Nothing found.</body></html>")

    deps = Deps(untappd_fetcher=lambda: (fetch_page, lambda: None), sleep=lambda s: None)
    alerter = run_mod.Alerter(state)

    run_mod.collect_untappd(state, config, run_mod.Corrections(), NOW, deps, alerter)

    bar_i = urls.index("https://untappd.com/b/bar/1")
    shop_i = urls.index("https://untappd.com/b/shop/2")
    search_i = next(i for i, u in enumerate(urls) if u.startswith("https://untappd.com/search?q="))
    country_i = urls.index("https://untappd.com/b/zagovor/3")
    assert bar_i < shop_i < search_i < country_i


# --- v1.1 boost settings: temporary Untappd budget increase ------------------

def test_collect_untappd_uses_boosted_daily_pages_and_search_cap():
    state = empty_state(NOW)
    state.pairs = {"parma": {
        f"n:beer{i}": _shop_pair("Brand", f"Beer {i}", last_seen=iso(NOW - timedelta(days=i)))
        for i in range(1, 11)
    }}
    config = Config(places={}, breweries=(), settings=Settings(
        untappd_daily_pages=40, boost_until="2026-12-31", boost_daily_pages=80, boost_search_per_run=10))
    urls = []

    def fetch_page(url):
        urls.append(url)
        return HttpResponse(200, {}, "<html><body>Nothing found.</body></html>")

    deps = Deps(untappd_fetcher=lambda: (fetch_page, lambda: None), sleep=lambda s: None)
    alerter = run_mod.Alerter(state)

    _, client = run_mod.collect_untappd(state, config, run_mod.Corrections(), NOW, deps, alerter)

    assert client.daily_pages == 80                                              # boosted, not the plain 40
    searches = [u for u in urls if u.startswith("https://untappd.com/search?q=")]
    assert len(searches) == 10                                                    # boosted cap, not the plain 8


def test_collect_untappd_uses_normal_budget_and_cap_when_not_boosted():
    state = empty_state(NOW)
    state.pairs = {"parma": {
        f"n:beer{i}": _shop_pair("Brand", f"Beer {i}", last_seen=iso(NOW - timedelta(days=i)))
        for i in range(1, 11)
    }}
    config = Config(places={}, breweries=(), settings=Settings(untappd_daily_pages=40))   # no boost fields set
    urls = []

    def fetch_page(url):
        urls.append(url)
        return HttpResponse(200, {}, "<html><body>Nothing found.</body></html>")

    deps = Deps(untappd_fetcher=lambda: (fetch_page, lambda: None), sleep=lambda s: None)
    alerter = run_mod.Alerter(state)

    _, client = run_mod.collect_untappd(state, config, run_mod.Corrections(), NOW, deps, alerter)

    assert client.daily_pages == 40
    searches = [u for u in urls if u.startswith("https://untappd.com/search?q=")]
    assert len(searches) == 8


def test_shop_match_refresh_candidates_oldest_checked_first_capped_at_three():
    state = empty_state(NOW)
    state.shop_matches = {
        f"n:beer{i}": ShopMatchRec(untappd_beer_id=i, url=f"https://untappd.com/b/x/{i}",
                                   checked_at=iso(NOW - timedelta(days=31 + i)))
        for i in range(1, 5)
    }
    candidates = run_mod._shop_match_refresh_candidates(state, NOW)
    assert [key for key, _ in candidates] == ["n:beer4", "n:beer3", "n:beer2"]   # oldest checked_at first


def test_shop_match_refresh_candidates_skips_fresh_and_unmatched():
    state = empty_state(NOW)
    state.shop_matches = {
        "n:fresh": ShopMatchRec(untappd_beer_id=1, url="u1", checked_at=iso(NOW - timedelta(days=1))),
        "n:no-match": ShopMatchRec(matched_at=iso(NOW - timedelta(days=40))),
    }
    assert run_mod._shop_match_refresh_candidates(state, NOW) == []


def test_refresh_shop_matches_updates_cached_fields():
    state = empty_state(NOW)
    state.shop_matches = {"n:kilikia": ShopMatchRec(untappd_beer_id=1559917, url=BEER_URL, style="Old Style",
                                                    checked_at=iso(NOW - timedelta(days=31)))}
    client = _untappd_client({BEER_URL: BEER_PAGE_HTML})
    run_mod.refresh_shop_matches(state, client, NOW)
    match = state.shop_matches["n:kilikia"]
    assert (match.rating, match.style, match.abv, match.checked_at) == (3.82, "Fruit Beer", 6.2, iso(NOW))


def test_refresh_shop_matches_stamps_checked_at_even_when_unparseable():
    state = empty_state(NOW)
    state.shop_matches = {"n:kilikia": ShopMatchRec(untappd_beer_id=1559917, url=BEER_URL, rating=3.5,
                                                    checked_at=iso(NOW - timedelta(days=31)))}
    client = _untappd_client({BEER_URL: "<html><body>login wall</body></html>"})
    run_mod.refresh_shop_matches(state, client, NOW)
    match = state.shop_matches["n:kilikia"]
    assert match.rating == 3.5 and match.checked_at == iso(NOW)   # kept, just stamped so it isn't refetched


def test_apply_shop_matches_overlays_matched_beer_onto_shop_pair():
    state = empty_state(NOW)
    state.pairs = {"parma": {"n:kilikia": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW),
        info={"kind": "shop", "name": "Kilikia", "brewery": "Kilikia", "url": "https://parma.am/p/1",
              "shop_url": "https://parma.am/p/1"})}}
    state.shop_matches["n:kilikia"] = ShopMatchRec(
        untappd_beer_id=1547626, url="https://untappd.com/b/kilikia-brewery-kilikia/1547626",
        rating=3.21, style="Pale Lager", abv=4.6, logo="https://x/logo.jpg",
        matched_at=iso(NOW), checked_at=iso(NOW))

    run_mod.apply_shop_matches(state)

    info = state.pairs["parma"]["n:kilikia"].info
    assert info["url"] == "https://untappd.com/b/kilikia-brewery-kilikia/1547626"
    assert info["shop_url"] == "https://parma.am/p/1"   # untouched: still the shop's own product page
    assert (info["rating"], info["style"], info["abv"], info["logo"]) == (3.21, "Pale Lager", 4.6, "https://x/logo.jpg")


def test_apply_shop_matches_leaves_unmatched_and_non_shop_pairs_alone():
    state = empty_state(NOW)
    state.pairs = {
        "parma": {"n:x": PairRec(first_seen=iso(NOW), last_seen=iso(NOW),
                                 info={"kind": "shop", "url": "https://parma.am/p/2"})},
        "gargoyle": {"u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "menu"})},
    }
    run_mod.apply_shop_matches(state)
    assert state.pairs["parma"]["n:x"].info["url"] == "https://parma.am/p/2"
    assert state.pairs["gargoyle"]["u:1"].info == {"kind": "menu"}


# --- apply_shop_matches: clears a stale identity once the match is gone (2nd review round) ----

def test_apply_shop_matches_clears_stale_identity_when_the_match_is_removed():
    """Code review round 2, finding B: a same_as untappd_id: null block or a corrections.yaml entry
    removed since the last run leaves no active shop_matches entry -- the previous run's
    u_name/u_brewery and Untappd url/rating/logo must not linger and show the wrong identity."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW),
        info={"kind": "shop", "name": "X", "brewery": "Y", "shop_url": "https://beer-city.am/p/1"})}}
    state.shop_matches["n:x"] = ShopMatchRec(
        untappd_beer_id=999, url="https://untappd.com/beer/999", rating=4.1, logo="https://x/logo.jpg",
        name="Stale Name", brewery="Stale Brewery", matched_at=iso(NOW), via="local")
    run_mod.apply_shop_matches(state)   # run 1: the match is active, its fields get overlaid
    del state.shop_matches["n:x"]       # run 2: the match is removed/blocked
    run_mod.apply_shop_matches(state)
    info = state.pairs["beer-city"]["n:x"].info
    assert "u_name" not in info and "u_brewery" not in info
    assert info["url"] == "https://beer-city.am/p/1"   # falls back to the shop's own product page
    assert "rating" not in info and "logo" not in info


def test_apply_shop_matches_switching_match_drops_the_previous_beers_identity():
    """Code review round 3, finding D: a same_as correction to a beer no bar has shown carries no
    name/brewery/logo, so the previous match's u_name/u_brewery/logo must not survive the switch."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW),
        info={"kind": "shop", "name": "X", "brewery": "Y", "shop_url": "https://beer-city.am/p/1"})}}
    state.shop_matches["n:x"] = ShopMatchRec(
        untappd_beer_id=2, url="https://untappd.com/beer/2", rating=3.9, logo="LOGO_A",
        name="Double IPA", brewery="Dargett Brewery", matched_at=iso(NOW), via="local", weak=True)
    run_mod.apply_shop_matches(state)
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=777, url="https://untappd.com/beer/777",
                                             matched_at=iso(NOW), via="manual")
    run_mod.apply_shop_matches(state)
    info = state.pairs["beer-city"]["n:x"].info
    assert info["url"] == "https://untappd.com/beer/777"
    assert not {"u_name", "u_brewery", "logo", "rating"} & info.keys()
    assert not info["match_weak"]


def test_apply_shop_matches_removed_match_takes_its_style_and_abv_back_but_keeps_the_shops_own():
    """A blocked false match (Beer City 'Bronx' vs The Bronx Brewery) must not leave Untappd's 6.3%
    on an 8% drink -- but a strength the shop scraped itself differs from the overlaid one and stays."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {
        "n:x": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "shop", "name": "X"}),
        "n:y": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "shop", "name": "Y"})}}
    for key in ("n:x", "n:y"):
        state.shop_matches[key] = ShopMatchRec(untappd_beer_id=9, url="https://untappd.com/beer/9", abv=6.3,
                                               style="Pale Ale - American", matched_at=iso(NOW), via="search")
    run_mod.apply_shop_matches(state)
    state.pairs["beer-city"]["n:y"].info["abv"] = 8.0   # the shop's own value arrives in a later run
    del state.shop_matches["n:x"], state.shop_matches["n:y"]
    run_mod.apply_shop_matches(state)
    x, y = state.pairs["beer-city"]["n:x"].info, state.pairs["beer-city"]["n:y"].info
    assert "abv" not in x and "style" not in x
    assert y["abv"] == 8.0 and "style" not in y


# --- apply_shop_matches: precise overlay-field tracking (3rd review round, finding B2) --------

def test_apply_shop_matches_clears_rating_even_when_a_full_run_already_reset_the_url():
    """Code review round 3, finding B2: a full shop run's own sighting always sets info["url"] to
    the shop's own product page (rules._update_info), BEFORE apply_shop_matches runs each run -- so
    by the time a removed match needs clearing, url is no longer the tell-tale Untappd url the
    round-2 fix's clearing was gated on. rating/logo must still be cleared, via a precise marker of
    which fields the overlay actually wrote, not the current url."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW),
        info={"kind": "shop", "name": "X", "shop_url": "https://beer-city.am/p/1"})}}
    state.shop_matches["n:x"] = ShopMatchRec(
        untappd_beer_id=999, url="https://untappd.com/beer/999", rating=4.1, logo="https://x/logo.jpg",
        matched_at=iso(NOW), via="local")
    run_mod.apply_shop_matches(state)
    del state.shop_matches["n:x"]
    # simulate this run's own full shop sighting already having reset url (rules._update_info runs
    # before apply_shop_matches every run) -- the round-2 fix's url-based gate is moot here
    state.pairs["beer-city"]["n:x"].info["url"] = "https://beer-city.am/p/1"

    run_mod.apply_shop_matches(state)
    info = state.pairs["beer-city"]["n:x"].info
    assert "rating" not in info and "logo" not in info
    assert info["url"] == "https://beer-city.am/p/1"


def test_apply_shop_matches_preserves_a_freshly_rescraped_shop_logo_after_the_match_is_removed():
    """Yerevan City re-sends its own shop photo into info["logo"] (and its own url) on every full
    run it finds the item (rules._update_info), BEFORE apply_shop_matches runs each run -- clearing
    a removed match's overlaid logo must not clobber that same-run refresh (full-run path)."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW),
        info={"kind": "shop", "name": "X", "shop_url": "https://yerevan-city.am/p/1"})}}
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=999, url="https://untappd.com/beer/999",
                                             logo="https://untappd.com/logo.jpg",
                                             matched_at=iso(NOW), via="local")
    run_mod.apply_shop_matches(state)
    assert state.pairs["beer-city"]["n:x"].info["logo"] == "https://untappd.com/logo.jpg"

    del state.shop_matches["n:x"]
    # simulate this run's own full shop sighting (rules._update_info, which runs before
    # apply_shop_matches every run) already resetting url to the shop's own page and refreshing
    # logo to Yerevan City's own current photo
    info = state.pairs["beer-city"]["n:x"].info
    info["url"] = info["shop_url"]
    info["logo"] = "https://media.yerevan-city.am/own-photo.jpg"

    run_mod.apply_shop_matches(state)
    assert state.pairs["beer-city"]["n:x"].info["logo"] == "https://media.yerevan-city.am/own-photo.jpg"


def test_apply_shop_matches_still_clears_a_stale_logo_when_nothing_refreshed_it():
    """Companion to the above (partial-run path): a partial Beer City run's _update_info never
    touches info at all for an already-known item (refresh=False) -- a removed match's overlaid
    logo/rating, left untouched since, must still be cleared."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "shop", "name": "X"})}}
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=999, logo="https://untappd.com/logo.jpg",
                                             rating=4.1, matched_at=iso(NOW), via="local")
    run_mod.apply_shop_matches(state)
    del state.shop_matches["n:x"]
    # no simulated refresh here -- info["logo"]/["rating"] are left exactly as the overlay wrote
    # them, as on a partial run of an already-known item
    run_mod.apply_shop_matches(state)
    info = state.pairs["beer-city"]["n:x"].info
    assert "logo" not in info and "rating" not in info


# --- apply_shop_matches: the weak-match flag (3rd review round, finding C2) ---------------------

def test_apply_shop_matches_carries_the_weak_flag_into_info_for_the_site_row():
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "shop", "name": "X"})}}
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=1, matched_at=iso(NOW), via="local", weak=True)
    run_mod.apply_shop_matches(state)
    assert state.pairs["beer-city"]["n:x"].info["match_weak"] is True


def test_apply_shop_matches_drops_a_stale_weak_flag_when_a_stronger_match_takes_over():
    """A weak local match later overridden by a manual same_as (weak=False) must not leave a stale
    match_weak behind."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "shop", "name": "X"})}}
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=1, matched_at=iso(NOW), via="local", weak=True)
    run_mod.apply_shop_matches(state)
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=2, matched_at=iso(NOW), via="manual")
    run_mod.apply_shop_matches(state)
    assert state.pairs["beer-city"]["n:x"].info["match_weak"] is False


def test_apply_shop_matches_clears_the_weak_flag_when_the_match_is_removed():
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "shop", "name": "X"})}}
    state.shop_matches["n:x"] = ShopMatchRec(untappd_beer_id=1, matched_at=iso(NOW), via="local", weak=True)
    run_mod.apply_shop_matches(state)
    del state.shop_matches["n:x"]
    run_mod.apply_shop_matches(state)
    assert "match_weak" not in state.pairs["beer-city"]["n:x"].info


def test_weak_local_match_reaches_the_site_row_as_match_weak():
    """End to end (round 3, finding C2): match_shop_beers_locally records weak=True, apply_shop_matches
    carries it into the pair's info, and build_site_data exposes it as the row's match_weak so a
    review page can list weak matches first -- while an ordinary match's row says False."""
    from taps.site_data import build_site_data

    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:1": _u_pair("Sour (Raspberry)", "Dargett Brewery"),
                    "u:2": _u_pair("Oatmeal Stout", "Dargett Brewery")},
        "beer-city": {"n:dargett sour": _shop_pair("Dargett", "Dargett Sour"),
                      "n:dargett oatmeal stout": _shop_pair("Dargett", "Dargett Oatmeal Stout")},
    }
    run_mod.match_shop_beers_locally(state, NOW)
    run_mod.apply_shop_matches(state)
    config = Config(places={"beer-city": Place(id="beer-city", name="Beer City", kind="shop",
                                               sources={"beercity": {}})},
                    breweries=(), settings=Settings())
    rows = {r["beer_key"]: r for r in build_site_data(state, config, NOW)["rows"]}
    assert (rows["n:dargett sour"]["match_weak"], rows["n:dargett oatmeal stout"]["match_weak"]) == (True, False)


def test_apply_shop_matches_clears_untappd_url_entirely_when_no_shop_url_is_on_file():
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW),
        info={"kind": "shop", "name": "X", "url": "https://untappd.com/beer/999"})}}
    run_mod.apply_shop_matches(state)
    assert "url" not in state.pairs["beer-city"]["n:x"].info


def test_apply_shop_matches_leaves_style_abv_and_a_non_untappd_url_alone_when_unmatched():
    """style/abv can be genuinely scraped by the shop itself (unlike rating/logo, always
    Untappd's), so they are not cleared just because there is no active match."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:x": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW),
        info={"kind": "shop", "name": "X", "url": "https://beer-city.am/p/1", "style": "IPA", "abv": 5.5})}}
    run_mod.apply_shop_matches(state)
    info = state.pairs["beer-city"]["n:x"].info
    assert info["url"] == "https://beer-city.am/p/1" and (info["style"], info["abv"]) == ("IPA", 5.5)


def test_apply_shop_matches_never_touches_a_native_untappd_keyed_pairs_own_url():
    """A bar's own "u:"-keyed menu pair never goes through shop_matches at all (only "n:" shop/menu/
    manual pairs do) -- its own, genuine Untappd url must survive the cleanup above untouched."""
    state = empty_state(NOW)
    state.pairs = {"gargoyle": {"u:1": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW),
        info={"kind": "menu", "name": "Celebrator", "url": "https://untappd.com/b/ayinger-celebrator/1"})}}
    run_mod.apply_shop_matches(state)
    assert state.pairs["gargoyle"]["u:1"].info["url"] == "https://untappd.com/b/ayinger-celebrator/1"


def test_apply_shop_matches_overlays_canonical_name_and_brewery_separately():
    """v1.2 beer identity: a matched shop row carries the Untappd beer's own name/brewery in
    separate u_name/u_brewery fields (e.g. so the site can group/display it correctly) -- the
    shop's own info["name"]/["brewery"] are never overwritten, so the digest and rules.py's brand
    classification (code review) keep seeing the shop's own, familiar text."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:dargett apricot ale": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW),
        info={"kind": "shop", "name": "Dargett apricot ale", "brewery": "Dargett"})}}
    state.shop_matches["n:dargett apricot ale"] = ShopMatchRec(
        untappd_beer_id=1674726, name="Apricot Ale (Prunus Armeniaca)", brewery="Dargett Brewery",
        matched_at=iso(NOW), via="local")
    run_mod.apply_shop_matches(state)
    info = state.pairs["beer-city"]["n:dargett apricot ale"].info
    assert (info["u_name"], info["u_brewery"]) == ("Apricot Ale (Prunus Armeniaca)", "Dargett Brewery")
    assert (info["name"], info["brewery"]) == ("Dargett apricot ale", "Dargett")   # untouched


def _rodenbach_state(match, beer=None):
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:rodenbach": _shop_pair("Rodenbach", "Rodenbach fruitage")}}
    state.shop_matches["n:rodenbach"] = match
    if beer:
        state.beers["u:1715344"] = beer
    return state


def test_apply_shop_matches_falls_back_to_the_beer_page_name_and_brewery():
    state = _rodenbach_state(_nameless_match(),
                             BeerRec(first_seen_city="x", name="Rodenbach Fruitage", brewery="Brouwerij Rodenbach"))
    run_mod.apply_shop_matches(state)
    info = state.pairs["beer-city"]["n:rodenbach"].info
    assert (info["u_name"], info["u_brewery"]) == ("Rodenbach Fruitage", "Brouwerij Rodenbach")
    assert info["name"] == "Rodenbach fruitage"


def test_apply_shop_matches_explicit_name_wins_over_the_beer_page():
    state = _rodenbach_state(_nameless_match(name="Fruitage", brewery="Rodenbach"),
                             BeerRec(first_seen_city="x", name="Rodenbach Fruitage", brewery="Brouwerij Rodenbach"))
    run_mod.apply_shop_matches(state)
    info = state.pairs["beer-city"]["n:rodenbach"].info
    assert (info["u_name"], info["u_brewery"]) == ("Fruitage", "Rodenbach")


def test_apply_shop_matches_page_name_goes_when_the_match_goes():
    state = _rodenbach_state(_nameless_match(),
                             BeerRec(first_seen_city="x", name="Rodenbach Fruitage", brewery="Brouwerij Rodenbach"))
    run_mod.apply_shop_matches(state)
    del state.shop_matches["n:rodenbach"]
    run_mod.apply_shop_matches(state)
    info = state.pairs["beer-city"]["n:rodenbach"].info
    assert "u_name" not in info and "u_brewery" not in info


def test_apply_shop_matches_also_overlays_menu_and_manual_kind_pairs():
    """Dargett Brewpub (buyam, kind "menu") and a friend's manual sighting can be matched too."""
    state = empty_state(NOW)
    state.pairs = {
        "dargett-brewpub": {"n:apricot ale": PairRec(
            first_seen=iso(NOW), last_seen=iso(NOW),
            info={"kind": "menu", "source": "buyam", "name": "Apricot Ale", "brewery": "Dargett"})},
        "tap-station": {"n:hazy pale": PairRec(
            first_seen=iso(NOW), last_seen=iso(NOW),
            info={"kind": "manual", "name": "Hazy Pale", "brewery": "379"})},
    }
    state.shop_matches["n:apricot ale"] = ShopMatchRec(untappd_beer_id=1, url="https://untappd.com/beer/1",
                                                        matched_at=iso(NOW), via="local")
    state.shop_matches["n:hazy pale"] = ShopMatchRec(untappd_beer_id=2, url="https://untappd.com/beer/2",
                                                      matched_at=iso(NOW), via="local")
    run_mod.apply_shop_matches(state)
    assert state.pairs["dargett-brewpub"]["n:apricot ale"].info["url"] == "https://untappd.com/beer/1"
    assert state.pairs["tap-station"]["n:hazy pale"].info["url"] == "https://untappd.com/beer/2"


def test_site_row_falls_back_to_shops_own_identity_after_a_null_block():
    """Code review round 2, finding B, the reviewer's exact scenario: corrections.yaml same_as
    untappd_id: null must not leave the site showing the previous run's stale canonical name/
    brewery/group_key -- it must fall back to the shop's own, exactly like an unmatched item."""
    from taps.site_data import build_site_data

    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:chimay peres trappistes blue": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW),
        info={"kind": "shop", "source": "beercity", "name": "Chimay peres trappistes blue", "brewery": "Chimay",
              "u_name": "Chimay Grande Réserve (Blue)", "u_brewery": "Bières de Chimay",
              "url": "https://untappd.com/beer/34039"})}}
    corrections = run_mod.Corrections(same_as={("beer-city", "n:chimay peres trappistes blue"): None})
    run_mod.apply_same_as(state, corrections, NOW)
    run_mod.apply_shop_matches(state)
    config = Config(places={"beer-city": Place(id="beer-city", name="Beer City", kind="shop",
                                               sources={"beercity": {}})},
                    breweries=(), settings=Settings())
    rows = build_site_data(state, config, NOW)["rows"]
    assert len(rows) == 1
    assert (rows[0]["name"], rows[0]["brewery"], rows[0]["group_key"]) == (
        "Chimay peres trappistes blue", "Chimay", "n:chimay peres trappistes blue")


def test_digest_line_for_a_matched_shop_pair_uses_the_untappd_name_and_brewery():
    """A shop pair matched to Untappd is announced under Untappd's canonical beer name and brewery
    (info["u_name"]/["u_brewery"], set by apply_shop_matches); the pair key stays the shop's."""
    state = empty_state(NOW)
    state.pairs = {"beer-city": {"n:chimay peres trappistes blue": PairRec(
        first_seen=iso(NOW), last_seen=iso(NOW), event_at=iso(NOW),
        info={"kind": "shop", "name": "Chimay peres trappistes blue", "brewery": "Chimay"})}}
    state.shop_matches["n:chimay peres trappistes blue"] = ShopMatchRec(
        untappd_beer_id=34039, name="Chimay Grande Réserve (Blue)", brewery="Bières de Chimay",
        matched_at=iso(NOW), via="local")
    run_mod.apply_shop_matches(state)
    config = Config(places={"beer-city": Place(id="beer-city", name="Beer City", kind="shop",
                                               sources={"beercity": {}})},
                    breweries=(), settings=Settings())
    digest = build_digest(state, config, config.settings, NOW)
    assert "Chimay Grande Réserve (Blue) — Bières de Chimay" in digest.html
    assert "Chimay peres trappistes blue" not in digest.html


def test_discovery_report_pluralizes_checkins_correctly(world):
    first_run(world)

    def add_venues(state):
        state.venues["1"] = VenueRec(
            name="Four Checkins", url="https://untappd.com/v/four/1", city="Yerevan", country="Armenia",
            checkins=[{"id": i, "at": iso(MONDAY_MORNING - timedelta(days=1))} for i in range(4)])
        state.venues["2"] = VenueRec(
            name="Eleven Checkins", url="https://untappd.com/v/eleven/2", city="Yerevan", country="Armenia",
            checkins=[{"id": i, "at": iso(MONDAY_MORNING - timedelta(days=1))} for i in range(11)])
    world.edit_state(add_venues)
    world.next_run()

    assert world.run(MONDAY_MORNING, no_digest=True) == 0

    discovery = next(s for s in world.sends if "Новые места по чекинам" in s["text"])
    assert "4 чекина" in discovery["text"]
    assert "11 чекинов" in discovery["text"]


def test_weekly_discovery_report_caps_at_20_venues_and_marks_only_those_reported(world):
    first_run(world)

    def add_many(state):
        for i in range(25):
            state.venues[str(90000 + i)] = VenueRec(
                name=f"Venue {i:02d}", url=f"https://untappd.com/v/venue-{i:02d}/{90000 + i}", city="Yerevan", country="Armenia",
                checkins=[{"id": i, "at": iso(MONDAY_MORNING - timedelta(days=1))} for _ in range(3)])
    world.edit_state(add_many)
    world.next_run()

    assert world.run(MONDAY_MORNING, no_digest=True) == 0

    discovery = next(s for s in world.sends if "Новые места по чекинам" in s["text"])
    lines = [line for line in discovery["text"].splitlines() if line.startswith("• ")]
    assert len(lines) == 20
    assert "…и ещё 5" in discovery["text"]
    assert len(discovery["text"]) <= MAX_TEXT
    assert len(world.state().discovery.reported) == 20


def test_weekly_discovery_report_stays_under_telegram_text_limit_with_long_names(world):
    first_run(world)

    def add_many(state):
        for i in range(20):
            long_name = "Очень Длинное Название Заведения " * 6 + str(i)
            state.venues[str(91000 + i)] = VenueRec(
                name=long_name, url=f"https://untappd.com/v/venue-{i:02d}/{91000 + i}", city="Yerevan", country="Armenia",
                checkins=[{"id": i, "at": iso(MONDAY_MORNING - timedelta(days=1))} for _ in range(3)])
    world.edit_state(add_many)
    world.next_run()

    assert world.run(MONDAY_MORNING, no_digest=True) == 0

    discovery = next(s for s in world.sends if "Новые места по чекинам" in s["text"])
    assert len(discovery["text"]) <= MAX_TEXT
    lines = [line for line in discovery["text"].splitlines() if line.startswith("• ")]
    assert len(lines) < 20
    assert "…и ещё" in discovery["text"]


@pytest.mark.parametrize("status, code", [("sent", 0), ("rejected", 1), ("unknown", 1)])
def test_discovery_report_send_failure_alerts_admin(world, status, code):
    """I-3: a discovery message that (maybe) never arrived alerts the admin and fails the job,
    like a digest failure, but reported ids/last_report_date stay committed regardless (no rollback)."""
    first_run(world)

    def add_venue(state):
        state.venues["99999"] = VenueRec(
            name="KER U SUS", url="https://untappd.com/v/ker-u-sus/99999", city="Yerevan", country="Armenia",
            checkins=[{"id": i, "at": iso(MONDAY_MORNING - timedelta(days=1))} for i in range(3)])
    world.edit_state(add_venue)
    world.next_run(send=[SendOutcome("rejected", "Bad Request: chat not found"),
                         SendOutcome(status, "Bad Request: chat not found")])

    assert world.run(MONDAY_MORNING, no_digest=True) == code

    assert len(world.sends) == 2
    assert "отчёт о новых местах" in world.sends[1]["text"]
    assert world.state().discovery.reported == [99999]


def test_next_evening_new_beer_goes_to_admin_as_preview(world):
    first_run(world)
    assert world.run(NEXT_EVENING) == 0

    assert len(world.sends) == 1
    sent = world.sends[0]
    assert sent["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"] and sent["token"] == "tok"
    assert sent["button"] == ("Открыть список", ENV["SITE_URL"])
    assert "Black Sails" in sent["text"] and "Beatles Pub" in sent["text"]
    state = world.state()
    assert state.digest.sent_count == 1
    assert state.digest.last_sent_at == iso(NEXT_EVENING) and state.digest.last_sent_date == "2026-09-25"
    assert state.pairs["beatles"]["u:999001"].notified_at == iso(NEXT_EVENING)
    # the mark was pushed before sending; last_to_admin is only known once the send outcome is in, so
    # it needs a second push
    assert world.pushes[0]["state"]["digest"]["sent_count"] == 1
    assert len(world.pushes) == 2
    assert world.pushes[1]["state"]["digest"]["last_to_admin"] is True


def test_sent_digest_stores_its_message_id_and_that_it_went_to_the_admin(world):
    """So a later `edit-last-digest` knows which Telegram message to edit and which chat it is in."""
    first_run(world)
    world.send_outcomes = [SendOutcome("sent", message_id=4242)]

    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    assert state.digest.last_message_id == 4242
    assert state.digest.last_to_admin is True   # the first two digests are previews to the admin


def test_third_digest_to_the_chat_stores_last_to_admin_false(world):
    first_run(world)
    world.edit_state(lambda s: setattr(s.digest, "sent_count", 2))
    world.send_outcomes = [SendOutcome("sent", message_id=555)]

    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    assert state.digest.last_message_id == 555
    assert state.digest.last_to_admin is False


def test_rejected_digest_does_not_store_a_message_id(world):
    first_run(world)
    world.send_outcomes = [SendOutcome("rejected", "Bad Request: chat not found")]

    assert world.run(NEXT_EVENING) == 0

    assert world.state().digest.last_message_id is None


def test_site_data_marks_the_beer_the_digest_just_announced_as_new_and_star(world):
    first_run(world)
    assert world.run(NEXT_EVENING) == 0

    data = json.loads((world.repo / "site" / "data.json").read_text(encoding="utf-8"))
    row = next(r for r in data["rows"] if r["place_id"] == "beatles" and r["beer_key"] == "u:999001")
    assert row["new"] is True and row["star"] is True


def test_second_run_same_evening_sends_nothing(world):
    first_run(world)
    assert world.run(NEXT_EVENING) == 0
    later = NEXT_EVENING + timedelta(hours=2)

    def add_pending(state):
        rec = state.pairs["parma"][next(iter(state.pairs["parma"]))]
        rec.event_at, rec.notified_at = iso(later), None
    world.edit_state(add_pending)
    world.next_run()
    assert world.run(later) == 0

    assert world.sends == []
    assert world.untappd.started == 0            # Untappd was fetched 2 h ago
    assert world.state().digest.sent_count == 1


def test_third_digest_goes_to_the_chat(world):
    first_run(world)
    world.edit_state(lambda s: setattr(s.digest, "sent_count", 2))
    assert world.run(NEXT_EVENING) == 0

    assert [s["chat"] for s in world.sends] == [ENV["TELEGRAM_CHAT_ID"]]
    assert world.state().digest.sent_count == 3


def test_group_chat_digest_excludes_alert_text_even_with_a_failing_source(world, monkeypatch):
    first_run(world)
    world.edit_state(lambda s: setattr(s.digest, "sent_count", 2))

    def failing_parma(http, place, known, now, ba):
        return SourceResult(key=f"parma:{place.id}", source="parma", ok=False, error="network", place_id=place.id)
    monkeypatch.setattr(run_mod, "fetch_parma", failing_parma)
    world.next_run(untappd=FakeUntappd(WITH_EXTRA_BEER))

    assert world.run(NEXT_EVENING) == 0

    chat_sends = [s for s in world.sends if s["chat"] == ENV["TELEGRAM_CHAT_ID"]]
    admin_sends = [s for s in world.sends if s["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]]
    assert len(chat_sends) == 1 and "⚠️" not in chat_sends[0]["text"]
    assert len(admin_sends) == 1 and "⚠️" in admin_sends[0]["text"] and "parma" in admin_sends[0]["text"]


def test_stale_pending_event_is_dropped_silently_in_a_morning_run(world):
    first_run(world)
    morning = datetime(2026, 9, 25, 6, 17, tzinfo=timezone.utc)   # Fri 10:17 in Yerevan
    stale_since = morning - timedelta(days=4)
    picked = {}

    def add_stale_pending(state):
        beer_key = next(iter(state.pairs["parma"]))
        rec = state.pairs["parma"][beer_key]
        rec.event_at, rec.notified_at = iso(stale_since), None
        picked["beer_key"] = beer_key
    world.edit_state(add_stale_pending)
    world.next_run()

    assert world.run(morning) == 0

    assert world.sends == []
    rec = world.state().pairs["parma"][picked["beer_key"]]
    assert rec.notified_at == iso(morning)      # dropped, not sent
    assert rec.event_at == iso(stale_since)


def test_renamed_place_id_starts_a_fresh_silent_baseline(world):
    first_run(world)
    world.next_run()
    renamed = PLACES_YAML.replace("id: beer-city, name: Beer City", "id: beer-city-2, name: Beer City")
    (world.repo / "places.yaml").write_text(renamed, encoding="utf-8")

    assert world.run(NEXT_EVENING) == 0

    assert world.sends == []
    state = world.state()
    assert "beercity:beer-city-2" in state.sources and state.sources["beercity:beer-city-2"].baseline_done
    assert state.pairs["beer-city-2"] and all(
        rec.notified_at == "baseline" for rec in state.pairs["beer-city-2"].values())
    assert "beer-city" in state.pairs   # old pairs untouched, not yet pruned


def test_failed_push_sends_nothing_and_exits_1(world):
    first_run(world)
    world.push_results = [False]
    assert world.run(NEXT_EVENING) == 1
    assert world.sends == []
    assert len(world.pushes) == 1


@pytest.mark.parametrize("outcome, alert", [
    (SendOutcome("rejected", "Bad Request: chat not found"), "Telegram не принял сводку (Bad Request: chat not found)"),
    (SendOutcome("rejected", "Bad Request: group chat was upgraded", migrate_to_chat_id=-100777), "новый id: -100777"),
])
def test_rejected_digest_rolls_back_pushes_and_alerts_admin(world, outcome, alert):
    first_run(world)
    world.send_outcomes = [outcome]
    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    assert (state.digest.sent_count, state.digest.last_sent_at, state.digest.last_sent_date) == (0, None, None)
    assert state.pairs["beatles"]["u:999001"].notified_at is None      # the event waits for the next digest
    assert world.pushes[0]["state"]["digest"]["sent_count"] == 1
    assert world.pushes[1]["state"]["digest"]["sent_count"] == 0       # the rollback was pushed
    assert world.pushes[1]["state"]["pairs"]["beatles"]["u:999001"]["notified_at"] is None
    assert [s["chat"] for s in world.sends] == [ENV["TELEGRAM_ADMIN_CHAT_ID"]] * 2
    assert "Black Sails" in world.sends[0]["text"] and alert in world.sends[1]["text"]
    assert world.pushes[-1]["state"]["alerts"] == state.alerts and "digest" in state.alerts


def test_rejected_digest_with_failed_rollback_push_exits_1_and_next_run_does_not_resend(world):
    first_run(world)

    class GiveUpPush:
        """Simulates gitsync's give-up-and-reset (fix for commit_and_push): a push whose message
        names the rollback fails and restores local state.json to the last one that did land."""
        def __init__(self):
            self.remote_state = None

        def __call__(self, repo, paths, message):
            content = (repo / "state.json").read_bytes()
            if "отменена" in message:
                (repo / "state.json").write_bytes(self.remote_state)
                return False
            self.remote_state = content
            return True

    world.next_run(untappd=FakeUntappd(WITH_EXTRA_BEER),
                   send=[SendOutcome("rejected", "Bad Request: chat not found")])
    deps = Deps(http=world.http, untappd_fetcher=world.untappd, send=world.send,
               pull=world.pulls.append, push=GiveUpPush(), sleep=lambda s: None)

    assert run(world.repo, NEXT_EVENING, ENV, deps) == 1
    assert len(world.sends) == 1   # only the rejected digest attempt: the recovery push failed first

    world.next_run()
    assert world.run(NEXT_EVENING + timedelta(hours=1)) == 0
    assert world.sends == []       # no resend: the first (successfully pushed) mark stands


def test_broken_corrections_with_snapshot_falls_back_silently_and_exits_0(world):
    first_run(world)
    good_snapshot = world.state().corrections_snapshot
    world.next_run()
    (world.repo / "corrections.yaml").write_text("sightings: [\n", encoding="utf-8")

    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    assert state.corrections_snapshot == good_snapshot   # kept as is: the broken file was never stored
    assert any(k.startswith("corrections:") for k in state.alerts)   # a soft warning, not a fatal alert


def test_unknown_digest_outcome_keeps_the_mark_and_alerts(world):
    first_run(world)
    world.send_outcomes = [SendOutcome("unknown", "ReadTimeout: timed out")]
    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    assert state.digest.sent_count == 1
    assert state.pairs["beatles"]["u:999001"].notified_at == iso(NEXT_EVENING)
    assert len(world.sends) == 2
    assert "сводка, возможно, не дошла (ReadTimeout: timed out)" in world.sends[1]["text"]
    assert world.sends[1]["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]


# --- reply tracking (getUpdates) and delete-if-unreplied ---------------------------------------

DELETE_T1 = NEXT_EVENING                        # first real group digest
DELETE_T2 = NEXT_EVENING + timedelta(days=1)    # a later group digest that may replace it


def _add_pending_event(state, when):
    """A fresh pending event (reusing an existing baseline pair) so a digest is due at `when`."""
    rec = state.pairs["parma"][next(iter(state.pairs["parma"]))]
    rec.event_at, rec.notified_at = iso(when), None


def _send_first_group_digest(world, message_id):
    """sent_count=2 skips both admin previews, so this digest goes straight to the group chat --
    the starting point for the reply/deletion tests below."""
    first_run(world)
    world.edit_state(lambda s: setattr(s.digest, "sent_count", 2))
    world.send_outcomes = [SendOutcome("sent", message_id=message_id)]
    assert world.run(DELETE_T1) == 0
    assert (world.state().digest.last_message_id, world.state().digest.last_to_admin) == (message_id, False)


def test_is_target_chat_matches_numeric_id_regardless_of_type():
    assert run_mod._is_target_chat({"id": -100123}, "-100123") is True
    assert run_mod._is_target_chat({"id": -100123}, "-100999") is False


def test_is_target_chat_matches_at_username_case_insensitively():
    assert run_mod._is_target_chat({"id": 555, "username": "yerevan_beer"}, "@yerevan_beer") is True
    assert run_mod._is_target_chat({"id": 555, "username": "Yerevan_Beer"}, "@yerevan_beer") is True
    assert run_mod._is_target_chat({"id": 555, "username": "other"}, "@yerevan_beer") is False


def test_track_digest_replies_marks_last_replied_when_a_reply_matches():
    state = empty_state(NOW)
    state.digest.last_message_id = 100
    pages = iter([[{"update_id": 1, "message": {"chat": {"id": ENV["TELEGRAM_CHAT_ID"]},
                                                "reply_to_message": {"message_id": 100}}}], []])

    ok = run_mod.track_digest_replies(state, ENV, Deps(get_updates=lambda token, offset: next(pages)))

    assert ok is True
    assert state.digest.last_replied is True
    assert state.telegram_offset == 2


def test_track_digest_replies_ignores_a_reply_to_a_different_message():
    state = empty_state(NOW)
    state.digest.last_message_id = 100
    pages = iter([[{"update_id": 1, "message": {"chat": {"id": ENV["TELEGRAM_CHAT_ID"]},
                                                "reply_to_message": {"message_id": 999}}}], []])

    run_mod.track_digest_replies(state, ENV, Deps(get_updates=lambda token, offset: next(pages)))

    assert state.digest.last_replied is False


def test_track_digest_replies_ignores_a_reply_from_a_different_chat():
    state = empty_state(NOW)
    state.digest.last_message_id = 100
    pages = iter([[{"update_id": 1, "message": {"chat": {"id": "some-other-chat"},
                                                "reply_to_message": {"message_id": 100}}}], []])

    run_mod.track_digest_replies(state, ENV, Deps(get_updates=lambda token, offset: next(pages)))

    assert state.digest.last_replied is False


def test_track_digest_replies_returns_false_on_getupdates_failure():
    state = empty_state(NOW)
    assert run_mod.track_digest_replies(state, ENV, Deps(get_updates=lambda token, offset: None)) is False


def test_offset_persists_and_is_reused_as_the_next_runs_starting_point(world):
    first_run(world)
    world.next_run(get_updates=[[{"update_id": 77, "message": {"chat": {"id": "other"}}}]])
    assert world.run(NEXT_EVENING) == 0
    assert world.state().telegram_offset == 78

    world.next_run()
    assert world.run(NEXT_EVENING + timedelta(hours=21)) == 0

    assert world.get_updates_calls[0]["offset"] == 78


def test_reply_tracking_never_stores_message_text_or_usernames(world):
    first_run(world)
    chatty_update = {"update_id": 20, "message": {
        "message_id": 321, "text": "воскресенье наконец никого нет опять?",
        "from": {"id": 999888777, "username": "some_real_person", "first_name": "Аня"},
        "chat": {"id": ENV["TELEGRAM_CHAT_ID"]},
    }}
    world.next_run(get_updates=[[chatty_update]])

    assert world.run(NEXT_EVENING) == 0

    raw = (world.repo / "state.json").read_text(encoding="utf-8")
    assert "some_real_person" not in raw and "Аня" not in raw and "воскресенье" not in raw


def test_no_reply_deletes_the_previous_group_digest_after_the_new_one_sends(world):
    _send_first_group_digest(world, message_id=100)
    world.edit_state(lambda s: _add_pending_event(s, DELETE_T2 - timedelta(hours=1)))
    world.next_run(send=[SendOutcome("sent", message_id=200)])

    assert world.run(DELETE_T2) == 0

    assert world.deletes == [{"token": "tok", "chat": ENV["TELEGRAM_CHAT_ID"], "message_id": 100}]
    assert world.state().digest.last_message_id == 200
    assert world.state().digest.last_replied is False


def test_reply_to_previous_digest_prevents_its_deletion(world):
    _send_first_group_digest(world, message_id=100)
    world.edit_state(lambda s: _add_pending_event(s, DELETE_T2 - timedelta(hours=1)))
    reply = {"update_id": 5001, "message": {"chat": {"id": ENV["TELEGRAM_CHAT_ID"]},
                                            "reply_to_message": {"message_id": 100}}}
    world.next_run(send=[SendOutcome("sent", message_id=200)], get_updates=[[reply]])

    assert world.run(DELETE_T2) == 0

    assert world.deletes == []
    assert world.state().digest.last_message_id == 200


def test_rejected_new_group_digest_does_not_delete_the_previous_one(world):
    _send_first_group_digest(world, message_id=100)
    world.edit_state(lambda s: _add_pending_event(s, DELETE_T2 - timedelta(hours=1)))
    world.next_run(send=[SendOutcome("rejected", "Bad Request: chat not found")])

    assert world.run(DELETE_T2) == 0

    assert world.deletes == []


def test_getupdates_failure_this_run_skips_deletion(world):
    _send_first_group_digest(world, message_id=100)
    world.edit_state(lambda s: _add_pending_event(s, DELETE_T2 - timedelta(hours=1)))
    world.next_run(send=[SendOutcome("sent", message_id=200)], get_updates=[None])

    assert world.run(DELETE_T2) == 0

    assert world.deletes == []
    assert world.state().digest.last_message_id == 200   # the new digest still sends and is tracked


def test_previous_digest_older_than_47_hours_is_not_deleted(world):
    _send_first_group_digest(world, message_id=100)
    later = DELETE_T1 + timedelta(hours=48)
    world.edit_state(lambda s: _add_pending_event(s, later - timedelta(hours=1)))
    world.next_run(send=[SendOutcome("sent", message_id=200)])

    assert world.run(later) == 0

    assert world.deletes == []


def test_admin_preview_digests_are_never_deleted_and_never_trigger_deletion(world):
    first_run(world)
    world.send_outcomes = [SendOutcome("sent", message_id=11)]
    assert world.run(NEXT_EVENING) == 0                                   # 1st digest: admin preview
    assert world.deletes == []

    later1 = NEXT_EVENING + timedelta(days=1)
    world.edit_state(lambda s: _add_pending_event(s, later1 - timedelta(hours=1)))
    world.next_run(send=[SendOutcome("sent", message_id=22)])
    assert world.run(later1) == 0                                        # 2nd digest: still a preview
    assert world.state().digest.last_to_admin is True
    assert world.deletes == []                                          # sending a preview never deletes

    later2 = later1 + timedelta(days=1)
    world.edit_state(lambda s: _add_pending_event(s, later2 - timedelta(hours=1)))
    world.next_run(send=[SendOutcome("sent", message_id=33)])
    assert world.run(later2) == 0                                        # 3rd digest: first real group one

    assert world.deletes == []          # message 22 was a preview: never a deletion target


def test_untappd_is_not_fetched_before_20_hours(world):
    first_run(world)
    world.edit_state(lambda s: setattr(s.untappd, "last_attempt", iso(NEXT_EVENING - timedelta(hours=5))))
    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    assert world.untappd.started == 0 and world.untappd.urls == []
    assert state.sources["untappd_menu:beatles"].last_ok == iso(NOW)          # not a failure either
    assert state.sources["untappd_menu:beatles"].fail_streak == 0
    assert state.sources["parma:parma"].last_ok == iso(NEXT_EVENING)          # shops ran
    assert world.sends == []                                                  # the new beer was not seen


def test_first_cloudflare_challenge_stops_untappd_but_not_shops(world):
    world.untappd = FakeUntappd(challenge=True)
    assert world.run(NOW) == 0

    state = world.state()
    assert world.untappd.urls == [GARGOYLE]                                   # nothing after the challenge
    assert state.sources["untappd_menu:gargoyle"].last_error == "cloudflare"
    for key in ("untappd_menu:beatles", "untappd_brewery:265165", "untappd_checkins:craft-story"):
        assert (state.sources[key].last_error, state.sources[key].fail_streak) == ("blocked", 1)
    for key in ("beercity:beer-city", "yerevan_city:yerevan-city", "parma:parma", "buyam:dargett-brewpub"):
        assert state.sources[key].baseline_done
    assert state.untappd.last_attempt == iso(NOW)                             # Untappd did answer
    assert len(world.sends) == 1 and world.sends[0]["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]
    assert world.sends[0]["text"].count("\n• ") == 1 and "Cloudflare" in world.sends[0]["text"]


def test_sources_out_of_untappd_budget_are_skipped_without_failure(world):
    state = empty_state(NOW)
    state.untappd.pages_today, state.untappd.pages_date = 39, "2026-09-24"   # 1 of 40 pages left
    save_state(world.repo / "state.json", state)
    assert world.run(NOW) == 0

    state = world.state()
    assert world.untappd.urls == [GARGOYLE]            # its second tab and everything after: no budget
    assert not any(k.startswith("untappd") for k in state.sources)
    assert state.untappd.pages_today == 40 and state.alerts == {} and world.sends == []


# --- TAPS_DEBUG_DIR opt-in capture --------------------------------------------------------------

def test_taps_debug_dir_captures_html_of_a_failed_brewery_source(world, monkeypatch, tmp_path):
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("TAPS_DEBUG_DIR", str(debug_dir))
    broken = UNTAPPD_PAGES["https://untappd.com/brewery/265165"].replace(
        'data-checkin-id="', 'data-old-id="')   # changed markup -> parse_checkins finds none ("empty")
    world.untappd = FakeUntappd({**UNTAPPD_PAGES, "https://untappd.com/brewery/265165": broken})
    assert world.run(NOW) == 0

    written = (debug_dir / "untappd_brewery_265165.html").read_text(encoding="utf-8")
    assert written.splitlines()[0] == "<!-- https://untappd.com/brewery/265165 -->"
    assert "data-old-id=" in written


def test_taps_debug_dir_captures_html_of_a_failed_menu_source(world, monkeypatch, tmp_path):
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("TAPS_DEBUG_DIR", str(debug_dir))
    broken = BEATLES_HTML.replace('href="/b/', 'href="/x/')   # no beer links parse -> no sightings ("empty")
    world.untappd = FakeUntappd({**UNTAPPD_PAGES, BEATLES: broken})
    assert world.run(NOW) == 0

    written = (debug_dir / "untappd_menu_beatles.html").read_text(encoding="utf-8")
    assert written.splitlines()[0] == f"<!-- {BEATLES} -->"
    assert 'href="/x/' in written


def test_taps_debug_dir_captures_html_of_a_failed_checkins_source(world, monkeypatch, tmp_path):
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("TAPS_DEBUG_DIR", str(debug_dir))
    craft_story_url = "https://untappd.com/v/craft-story/12281551"
    # check-ins parse fine, but none at the tracked venue itself -> "bad_response"
    broken = UNTAPPD_PAGES[craft_story_url].replace("/v/craft-story/12281551", "/v/craft-story/99999999")
    world.untappd = FakeUntappd({**UNTAPPD_PAGES, craft_story_url: broken})
    assert world.run(NOW) == 0

    written = (debug_dir / "untappd_checkins_craft-story.html").read_text(encoding="utf-8")
    assert written.splitlines()[0] == f"<!-- {craft_story_url} -->"
    assert "/v/craft-story/99999999" in written


def test_taps_debug_dir_unset_writes_no_files_even_when_a_source_fails(world, monkeypatch, tmp_path):
    monkeypatch.delenv("TAPS_DEBUG_DIR", raising=False)
    debug_dir = tmp_path / "debug"
    broken = UNTAPPD_PAGES["https://untappd.com/brewery/265165"].replace(
        'data-checkin-id="', 'data-old-id="')
    world.untappd = FakeUntappd({**UNTAPPD_PAGES, "https://untappd.com/brewery/265165": broken})
    assert world.run(NOW) == 0
    assert not debug_dir.exists()


def test_broken_corrections_without_snapshot_stops_with_exit_2(world):
    (world.repo / "corrections.yaml").write_text("sightings: [\n", encoding="utf-8")
    assert world.run(NOW) == 2

    # the (empty) state loaded fine, so it is used for alert dedup and persisted
    assert (world.repo / "state.json").exists()
    assert not (world.repo / "site" / "data.json").exists()
    assert world.untappd.started == 0 and world.http.urls == []
    assert [p["paths"] for p in world.pushes] == [["state.json"]]
    assert len(world.sends) == 1 and world.sends[0]["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]
    assert "corrections.yaml не читается" in world.sends[0]["text"]


@pytest.mark.parametrize("break_repo", [
    lambda repo: (repo / "corrections.yaml").write_text("sightings: [\n", encoding="utf-8"),
    lambda repo: (repo / "state.json").write_text("{not json", encoding="utf-8"),
    lambda repo: (repo / "places.yaml").write_text("not: [a, valid\n", encoding="utf-8"),
], ids=["broken_corrections_no_snapshot", "corrupt_state_json", "broken_places_yaml"])
def test_fatal_alert_is_sent_once_per_series_across_runs(world, break_repo):
    break_repo(world.repo)
    assert world.run(NOW) == 2
    assert world.run(NOW) == 2   # same World: no next_run(), so world.sends accumulates across both calls

    assert len(world.sends) == 1
    assert world.sends[0]["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]


def test_fatal_alert_dedup_survives_a_fresh_checkout_via_state_json(world):
    """On GitHub every job starts from a clean checkout: the untracked marker is gone, state.json is not."""
    (world.repo / "places.yaml").write_text("not: [a, valid\n", encoding="utf-8")
    assert world.run(NOW) == 2
    assert "fatal" in world.state().alerts
    assert not (world.repo / ".taps-fatal").exists()       # state.json did the dedup, not the marker

    (world.repo / ".taps-fatal").unlink(missing_ok=True)   # a fresh checkout
    assert world.run(NOW) == 2

    assert len(world.sends) == 1
    assert [p["paths"] for p in world.pushes] == [["state.json"]] * 2


def test_fatal_alert_series_is_cleared_by_a_good_run_so_a_new_failure_alerts_again(world):
    (world.repo / "places.yaml").write_text("not: [a, valid\n", encoding="utf-8")
    assert world.run(NOW) == 2
    (world.repo / "places.yaml").write_text(PLACES_YAML, encoding="utf-8")
    world.next_run()
    assert world.run(NOW) == 0
    assert "fatal" not in world.state().alerts

    (world.repo / "places.yaml").write_text("not: [a, valid\n", encoding="utf-8")
    world.next_run()
    assert world.run(NOW + timedelta(hours=1)) == 2
    assert len(world.sends) == 1


def test_corrupt_state_json_dedups_through_the_marker_file(world):
    (world.repo / "state.json").write_text("{not json", encoding="utf-8")
    assert world.run(NOW) == 2
    assert (world.repo / ".taps-fatal").exists()
    assert world.run(NOW) == 2
    assert len(world.sends) == 1


def test_browser_that_does_not_start_alerts_once_and_shops_still_run(world):
    def broken_browser():
        raise RuntimeError("Executable doesn't exist at /home/runner/.cache/ms-playwright/chromium-1234")
    world.untappd = broken_browser
    assert world.run(NOW) == 0
    state = world.state()
    assert "untappd:browser" in state.alerts
    assert len(world.sends) == 1 and "браузер" in world.sends[0]["text"]
    assert state.sources["parma:parma"].baseline_done and not any(k.startswith("untappd") for k in state.sources)

    def other_error():
        raise RuntimeError("a different text on the next run")
    world.next_run(untappd=other_error)
    assert world.run(NOW + timedelta(hours=8)) == 0
    assert world.sends == []                     # same series, the text changed: still one alert


@pytest.mark.parametrize("status, code", [("sent", 0), ("rejected", 1), ("unknown", 1)])
def test_admin_alert_that_is_not_delivered_makes_the_run_exit_1(world, status, code):
    world.untappd = FakeUntappd(challenge=True)         # queues the single Cloudflare alert
    world.send_outcomes = [SendOutcome(status, "Bad Request: chat not found")]
    assert world.run(NOW) == code
    assert len(world.sends) == 1
    if status == "rejected":                            # the hashes were forgotten and that state was pushed
        assert "untappd:cloudflare" not in world.pushes[-1]["state"]["alerts"]


def test_no_digest_needs_only_the_bot_token_and_the_admin_chat(world):
    env = {k: ENV[k] for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_CHAT_ID")}
    assert run(world.repo, NOW, env, world.deps(), no_digest=True) == 0


def test_digest_run_still_needs_the_group_chat_and_site_url(world):
    env = {k: ENV[k] for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_CHAT_ID")}
    assert run(world.repo, NOW, env, world.deps()) == 2
    assert world.pulls == []


def test_dry_run_needs_no_environment_at_all(world):
    assert run(world.repo, NOW, {}, world.deps(), dry_run=True) == 0


def test_checkout_that_is_not_a_clean_main_stops_the_run_with_exit_2(world):
    def refuse(repo):
        raise CheckoutError("прогон только из ветки main, сейчас feat/v1: переключитесь на чистый main")
    deps = world.deps()
    deps.pull = refuse

    assert run(world.repo, NOW, ENV, deps) == 2

    assert world.pushes == [] and world.http.urls == [] and world.untappd.started == 0
    assert len(world.sends) == 1 and world.sends[0]["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]
    assert "feat/v1" in world.sends[0]["text"]


def test_dry_run_prints_no_digest_and_touches_no_git_or_telegram(world, capsys):
    assert world.run(NOW, dry_run=True) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0] == "нет сводки"
    assert '"rows"' in out
    assert world.pulls == world.pushes == world.sends == []
    assert not (world.repo / "state.json").exists()
    assert (world.repo / "site" / "data.json").exists()


def test_dry_run_prints_the_digest_and_the_site_data_without_marking_it(world, capsys):
    first_run(world)
    before = (world.repo / "state.json").read_bytes()
    capsys.readouterr()
    assert world.run(NEXT_EVENING, dry_run=True) == 0

    out = capsys.readouterr().out
    assert "Black Sails" in out and "Новое в Ереване" in out
    assert '"rows"' in out
    assert world.pulls == world.pushes == world.sends == []
    assert (world.repo / "state.json").read_bytes() == before


def test_no_digest_flag_skips_the_digest(world):
    first_run(world)
    assert world.run(NEXT_EVENING, no_digest=True) == 0
    assert world.sends == []
    assert world.state().pairs["beatles"]["u:999001"].notified_at is None


def test_missing_env_stops_before_anything(world):
    assert run(world.repo, NOW, {"TELEGRAM_BOT_TOKEN": "tok"}, world.deps()) == 2
    assert world.pulls == [] and world.http.urls == [] and not (world.repo / "state.json").exists()


def test_alert_series_dedupes_failures_by_key_and_resolves_on_success():
    """A failing source's error text may change (network -> http -> empty) without starting a new alert."""
    state = empty_state(NOW)
    a1 = Alerter(state)
    update_alerts(a1, state, MergeOutcome(failed=[("parma:parma", "network")]), None, [])
    assert a1.pending_text() is not None and "network" in a1.pending_text()

    a2 = Alerter(state)   # next run, same state
    update_alerts(a2, state, MergeOutcome(failed=[("parma:parma", "http")]), None, [])
    assert a2.pending_text() is None

    a3 = Alerter(state)
    update_alerts(a3, state, MergeOutcome(failed=[("parma:parma", "empty")]), None, [])
    assert a3.pending_text() is None

    a4 = Alerter(state)   # the source recovers: resolved
    update_alerts(a4, state, MergeOutcome(ok=["parma:parma"]), None, [])
    assert "source:parma:parma" not in state.alerts

    a5 = Alerter(state)   # a fresh failure after recovery alerts again
    update_alerts(a5, state, MergeOutcome(failed=[("parma:parma", "network")]), None, [])
    assert a5.pending_text() is not None


def test_trip_alerts_once_then_accept_alerts_once():
    state = empty_state(NOW)
    a1 = Alerter(state)
    update_alerts(a1, state, MergeOutcome(tripped=[("beercity:beer-city", "shrink")]), None, [])
    assert a1.pending_text() is not None

    a2 = Alerter(state)   # same trip reason next run: still one alert for the series
    update_alerts(a2, state, MergeOutcome(tripped=[("beercity:beer-city", "shrink")]), None, [])
    assert a2.pending_text() is None

    a3 = Alerter(state)   # accepted after 3 runs: a fresh, one-time acceptance message
    update_alerts(a3, state, MergeOutcome(ok=["beercity:beer-city"], accepted=["beercity:beer-city"]), None, [])
    assert a3.pending_text() is not None and "принял" in a3.pending_text()


def test_main_parses_args_and_env(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(run_mod, "run", lambda repo, now, env, deps, dry_run=False, no_digest=False:
                        calls.append((repo, now, env, deps, dry_run, no_digest)) or 7)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100chat")

    assert main(["run", "--dry-run", "--no-digest", "--repo", str(tmp_path)]) == 7
    repo, now, env, deps, dry_run, no_digest = calls[0]
    assert (repo, dry_run, no_digest) == (tmp_path, True, True)
    assert env["TELEGRAM_CHAT_ID"] == "-100chat" and now.tzinfo is not None
    assert (deps.send, deps.pull, deps.push) == (send_message, pull_ff, commit_and_push)

    assert main(["run"]) == 7
    assert calls[1][0] == Path(".") and calls[1][4:] == (False, False)
    with pytest.raises(SystemExit):
        main([])


def test_shop_match_candidates_skip_beers_not_shown_on_the_site():
    """Mass brands (filtered -> info hidden), out-of-stock items and items gone from the last listing
    are not on the site, so searching Untappd for them only burns the page budget."""
    state = empty_state(NOW)
    hidden = _shop_pair("Baltika", "Baltika 3")
    hidden.info["hidden"] = True
    oos = _shop_pair("Konix", "Bronx")
    oos.in_stock = False
    gone = _shop_pair("Gletcher", "Pale")
    gone.last_in_result = False
    state.pairs = {"parma": {"n:baltika 3": hidden, "n:bronx": oos, "n:pale": gone,
                             "n:kilikia": _shop_pair("Kilikia", "Kilikia")}}
    assert run_mod._shop_match_candidates(state, NOW) == [("n:kilikia", "Kilikia", "Kilikia", None)]


def test_apply_known_beer_info_gives_manual_pairs_label_and_rating_of_the_same_beer():
    state = empty_state(NOW)
    state.pairs = {
        "beatles": {"u:10722": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={
            "kind": "menu", "logo": "https://x/rochefort.jpg", "rating": 3.9, "style": "Belgian Dubbel"})},
        "ferment": {
            "u:10722": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={
                "kind": "manual", "name": "Trappistes Rochefort 6", "style": "Dubbel", "price_amd": 2500}),
            "u:5": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "manual", "name": "Cached"}),
            "n:x": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), info={"kind": "manual", "name": "X"}),
        },
    }
    state.beers["u:5"] = BeerRec(first_seen_city=iso(NOW), rating=3.5)

    run_mod.apply_known_beer_info(state)

    info = state.pairs["ferment"]["u:10722"].info
    assert (info["logo"], info["rating"]) == ("https://x/rochefort.jpg", 3.9)
    assert (info["style"], info["price_amd"]) == ("Dubbel", 2500)   # the board's own details stay
    assert state.pairs["ferment"]["u:5"].info["rating"] == 3.5 and "logo" not in state.pairs["ferment"]["u:5"].info
    assert state.pairs["ferment"]["n:x"].info == {"kind": "manual", "name": "X"}
    assert "price_amd" not in state.pairs["beatles"]["u:10722"].info


# --- several servings of one beer at one place (whole run) ---------------------------------------

def _gargoyle_with_price(size, price):
    """The Gargoyle page with a price block on its first beer, Dahook Hell (no captured beer row has prices)."""
    soup = BeautifulSoup(UNTAPPD_PAGES[GARGOYLE], "html.parser")
    soup.select_one("li.menu-item").append(BeautifulSoup(
        f'<div class="beer-prices"><p><span class="size">{size}</span><span class="price">{price}</span></p></div>',
        "html.parser"))
    return str(soup)


def _site_row(world, place_id, beer_key):
    data = json.loads((world.repo / "site" / "data.json").read_text(encoding="utf-8"))
    return next(r for r in data["rows"] if (r["place_id"], r["beer_key"]) == (place_id, beer_key))


def test_a_beer_that_gains_a_bottle_serving_on_the_menu_is_no_event_and_shows_both_on_the_site(world):
    assert world.run(NOW) == 0
    pages = {**UNTAPPD_PAGES, GARGOYLE: _gargoyle_with_price("0.5L Draft", "1,800.00 AMD"),
             GARGOYLE + "?menu_id=203568": _gargoyle_with_price("0.33L Bottle", "1,200.00 AMD")}
    world.next_run(untappd=FakeUntappd(pages))

    assert world.run(NEXT_EVENING) == 0

    assert world.sends == []                     # nothing new to announce
    rec = world.state().pairs["gargoyle"]["u:5817007"]
    assert (rec.event_at, rec.notified_at) == (None, "baseline")
    assert [(s["container"], s["price_amd"], s["volume_ml"]) for s in rec.info["servings"]] == [
        ("draft", 1800, 500), ("bottle", 1200, 330)]
    row = _site_row(world, "gargoyle", "u:5817007")
    assert (row["container"], row["price_amd"], row["volume_ml"]) == ("draft", 1800, 500)   # the first serving
    assert [s["container"] for s in row["servings"]] == ["draft", "bottle"]
    assert "servings" not in _site_row(world, "gargoyle", "u:5817002")    # the other beers stay single


def _board(*containers_and_prices):
    """corrections.yaml for one hand-entered beer, one entry per serving (same place, date and friend)."""
    entries = "".join(
        f"  - {{place: craft-story, untappd: 1715344, brewery: Rodenbach, beer: Fruitage, container: {container}, "
        f"{price}by: Аня, date: 2026-09-24}}\n" for container, price in containers_and_prices)
    return "sightings:\n" + entries


def test_hand_entered_servings_are_announced_as_one_beer_and_a_later_serving_is_not_announced_again(world):
    assert world.run(NOW) == 0
    world.next_run()
    (world.repo / "corrections.yaml").write_text(_board(("розлив", "price: 2800, "), ("бутылка", "")), encoding="utf-8")

    assert world.run(NEXT_EVENING) == 0

    [sent] = world.sends
    assert sent["text"].count("Fruitage") == 1                            # one line for the pair, not one per serving
    assert "<b>Craft Story</b> · со слов: Аня\n• Fruitage — Rodenbach" in sent["text"]
    assert world.state().announced_manual == ["craft-story|2026-09-24|Аня"]
    assert [s["container"] for s in _site_row(world, "craft-story", "u:1715344")["servings"]] == ["draft", "bottle"]

    # a friend adds a can the next day: the same beer at the same place, nothing to announce
    world.next_run()
    (world.repo / "corrections.yaml").write_text(
        _board(("розлив", "price: 2800, "), ("бутылка", ""), ("банка", "price: 900, ")), encoding="utf-8")
    assert world.run(NEXT_EVENING + timedelta(hours=3)) == 0

    assert world.sends == []
    row = _site_row(world, "craft-story", "u:1715344")
    assert [(s["container"], s["price_amd"]) for s in row["servings"]] == [("draft", 2800), ("bottle", None), ("can", 900)]
    assert row["new"] is True                                              # still the day's 🆕, not a second one


def test_a_beer_fixed_after_its_entries_were_announced_together_is_not_announced_again(world):
    assert world.run(NOW) == 0
    world.next_run()
    corrections = world.repo / "corrections.yaml"
    corrections.write_text(
        "sightings:\n"
        "  - {place: craft-story, untappd: 1715344, brewery: Rodenbach, beer: Fruitage, container: розлив,"
        " price: 2800, by: Аня, date: 2026-09-24}\n"
        "  - {place: craft-story, untappd: 1715344, brewery: Rodenbach, beer: Fruitage, container: бутылка,"
        " by: Олег, date: 2026-09-25}\n", encoding="utf-8")
    assert world.run(NEXT_EVENING) == 0
    assert len(world.sends) == 1                    # two friends' entries, one beer, one announcement
    assert world.state().announced_manual == ["craft-story|2026-09-24|Аня", "craft-story|2026-09-25|Олег"]

    # the first entry is deleted and the second one's beer is corrected: that is the same news, not a new beer
    world.next_run()
    corrections.write_text(
        "sightings:\n"
        "  - {place: craft-story, untappd: 1715345, brewery: Rodenbach, beer: Fruitage Rosé, container: бутылка,"
        " by: Олег, date: 2026-09-25}\n", encoding="utf-8")
    assert world.run(NEXT_EVENING + timedelta(days=1)) == 0
    assert world.sends == []
    assert world.state().pairs["craft-story"]["u:1715345"].notified_at == "suppressed"


# --- edit-last-digest: edit an already-sent digest message in place --------------------------------

def _digest_config():
    return Config(places={"gargoyle": Place(id="gargoyle", name="Gargoyle Bar", kind="bar", sources={})},
                 breweries=(), settings=Settings())


def test_rebuild_last_digest_includes_only_pairs_marked_at_that_exact_moment():
    state = empty_state(NOW)
    sent_at = iso(NOW - timedelta(hours=1))
    state.pairs = {"gargoyle": {
        "u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), event_at=iso(NOW - timedelta(hours=2)),
                      notified_at=sent_at, info={"name": "Announced Beer"}),
        "u:2": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), event_at=iso(NOW),
                      notified_at=None, info={"name": "Newer Pending Beer"}),   # pending from a later run
        "u:3": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), event_at=iso(NOW - timedelta(days=1)),
                      notified_at="baseline", info={"name": "Old Baseline Beer"}),
    }}
    digest = run_mod._rebuild_last_digest(state, _digest_config(), sent_at)
    assert digest is not None
    assert "Announced Beer" in digest.html
    assert "Newer Pending Beer" not in digest.html
    assert "Old Baseline Beer" not in digest.html
    # read-only: the original state is untouched
    assert state.pairs["gargoyle"]["u:2"].notified_at is None
    assert state.pairs["gargoyle"]["u:1"].notified_at == sent_at


def test_rebuild_last_digest_includes_brewery_new_marked_at_that_moment():
    state = empty_state(NOW)
    sent_at = iso(NOW - timedelta(hours=1))
    state.brewery_new = {
        "u:100": BreweryNewRec(brewery_id=1, found_at=iso(NOW - timedelta(hours=2)), notified_at=sent_at,
                               info={"name": "New Brewery Beer", "brewery": "Some Brewery"}),
        "u:200": BreweryNewRec(brewery_id=1, found_at=iso(NOW), notified_at=None,
                               info={"name": "Later Brewery Beer", "brewery": "Some Brewery"}),
    }
    digest = run_mod._rebuild_last_digest(state, _digest_config(), sent_at)
    assert digest is not None
    assert "New Brewery Beer" in digest.html
    assert "Later Brewery Beer" not in digest.html


def test_rebuild_last_digest_returns_none_when_nothing_was_sent_at_that_moment():
    state = empty_state(NOW)
    state.pairs = {"gargoyle": {"u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW), notified_at="baseline")}}
    assert run_mod._rebuild_last_digest(state, _digest_config(), iso(NOW)) is None


def test_rebuild_last_digest_dates_the_header_by_the_original_send_time():
    state = empty_state(NOW)
    sent_at = iso(NOW - timedelta(days=3))   # Mon 21 Sep 2026, not today's Thursday
    state.pairs = {"gargoyle": {"u:1": PairRec(first_seen=iso(NOW), last_seen=iso(NOW),
                                               event_at=iso(NOW - timedelta(days=3, hours=1)),
                                               notified_at=sent_at, info={"name": "X"})}}
    digest = run_mod._rebuild_last_digest(state, _digest_config(), sent_at)
    assert "пн, 21 сен" in digest.html


def test_edit_last_digest_uses_the_stored_message_id_chat_and_button(world):
    first_run(world)
    world.send_outcomes = [SendOutcome("sent", message_id=4242)]
    assert world.run(NEXT_EVENING) == 0
    before = (world.repo / "state.json").read_bytes()
    calls = []

    def fake_edit(token, chat_id, message_id, html, button=None):
        calls.append((token, chat_id, message_id, html, button))
        return SendOutcome("sent")

    assert edit_last_digest(world.repo, None, ENV, edit=fake_edit) == 0

    assert len(calls) == 1
    token, chat_id, message_id, html, button = calls[0]
    assert (token, chat_id, message_id) == ("tok", ENV["TELEGRAM_ADMIN_CHAT_ID"], 4242)
    assert button == (run_mod.BUTTON_TEXT, ENV["SITE_URL"])
    assert "Black Sails" in html
    assert (world.repo / "state.json").read_bytes() == before   # read-only: nothing written


def test_edit_last_digest_message_id_flag_overrides_the_stored_one(world):
    first_run(world)
    world.send_outcomes = [SendOutcome("sent", message_id=4242)]
    assert world.run(NEXT_EVENING) == 0
    calls = []

    def fake_edit(token, chat_id, message_id, html, button=None):
        calls.append(message_id)
        return SendOutcome("sent")

    assert edit_last_digest(world.repo, 999, ENV, edit=fake_edit) == 0
    assert calls == [999]


@pytest.mark.parametrize("bad_id", [0, -5])
def test_edit_last_digest_rejects_a_non_positive_message_id(world, bad_id, capsys):
    first_run(world)
    assert edit_last_digest(world.repo, bad_id, ENV) == 2
    assert capsys.readouterr().err.strip() != ""


def test_edit_last_digest_exits_2_when_no_message_id_is_available(world):
    first_run(world)   # a digest was never sent: no stored message id
    assert edit_last_digest(world.repo, None, ENV) == 2


def test_edit_last_digest_exits_2_when_a_digest_was_never_sent(world):
    first_run(world)   # message id supplied, but nothing to rebuild: no digest was ever sent
    assert edit_last_digest(world.repo, 123, ENV) == 2


def test_edit_last_digest_missing_env_exits_2(world):
    first_run(world)
    assert edit_last_digest(world.repo, 123, {"TELEGRAM_BOT_TOKEN": "tok"}) == 2


@pytest.mark.parametrize("status, code", [("sent", 0), ("rejected", 1), ("unknown", 1)])
def test_edit_last_digest_exit_code_follows_the_edit_outcome(world, status, code):
    first_run(world)
    world.send_outcomes = [SendOutcome("sent", message_id=4242)]
    assert world.run(NEXT_EVENING) == 0

    def fake_edit(token, chat_id, message_id, html, button=None):
        return SendOutcome(status, "подробности")

    assert edit_last_digest(world.repo, None, ENV, edit=fake_edit) == code


def test_main_parses_edit_last_digest_args(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(run_mod, "edit_last_digest",
                        lambda repo, message_id, env: calls.append((repo, message_id, env)) or 5)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")

    assert main(["edit-last-digest", "--message-id", "42", "--repo", str(tmp_path)]) == 5
    repo, message_id, env = calls[0]
    assert (repo, message_id) == (tmp_path, 42)
    assert env["TELEGRAM_BOT_TOKEN"] == "tok"

    assert main(["edit-last-digest"]) == 5
    assert calls[1][:2] == (Path("."), None)
