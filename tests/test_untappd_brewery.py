from dataclasses import replace
from datetime import datetime, timezone

import pytest

from taps.config import Brewery, Config, Place, Settings
from taps.fetch import FetchError
from taps.model import BreweryBeer, VenueCheckin
from taps.sources.untappd_brewery import BeerList, fetch_brewery_checkins, fetch_brewery_list, parse_beer_list
from taps.sources.untappd_checkins import checkins_to_sightings, parse_checkins
from tests.helpers import fixture_text

NOW = datetime(2025, 11, 16, 12, 0, tzinfo=timezone.utc)
DARGETT = Brewery(id="dargett", name="Dargett", brewery_id=265165, slug="dargett-brewery")
LIST_URL = "https://untappd.com/w/dargett-brewery/265165/beer"

# As in places.yaml: menu from buy.am, Untappd venue 4640403 ("Dargett Craft Brewery").
DARGETT_PUB = Place(
    id="dargett-brewpub", name="Dargett Brewpub", kind="brewpub",
    sources={"buyam": {"url": "https://buy.am/en/restaurants/dargett"}},
    brewery_id=265165, brewery_name="Dargett", untappd_venue_id=4640403)
# A Yerevan bar from the page's check-ins, added the way Oleg would add one.
FERMENT = Place(id="ferment", name="Ferment", kind="bar",
                sources={"untappd_checkins": {"slug": "ferment", "venue_id": 13510969}})
CONFIG = Config(places={p.id: p for p in (DARGETT_PUB, FERMENT)}, breweries=(DARGETT,), settings=Settings())


class FakeClient:
    """Stands in for UntappdClient: returns one page or raises one FetchError."""

    def __init__(self, html=None, error=None):
        self.html, self.error, self.urls = html, error, []

    def get(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.html


def brewery_page():
    return fixture_text("untappd/dargett_brewery.html")


# --- brewery page check-ins (real page) ---------------------------------------------------

def test_checkins_from_real_brewery_page():
    client = FakeClient(brewery_page())
    result = fetch_brewery_checkins(client, DARGETT, CONFIG, NOW, {})
    assert client.urls == ["https://untappd.com/brewery/265165"]
    assert (result.key, result.source, result.ok, result.error) == (
        "untappd_brewery:265165", "untappd_brewery", True, None)
    assert (result.brewery_id, result.place_id) == (265165, None)
    # 20 check-ins on the page. Untappd at Home (4), Caffe Napoli, Number 8 in Tbilisi (3)
    # and the 2 check-ins without a venue are not places of the config.
    assert [(s.place_id, s.checkin_id) for s in result.sightings] == [
        ("ferment", 1528689976), ("ferment", 1528688333),
        ("dargett-brewpub", 1528656512), ("dargett-brewpub", 1528654307),
        ("dargett-brewpub", 1528643628), ("dargett-brewpub", 1528642716),
        ("dargett-brewpub", 1528641971), ("dargett-brewpub", 1528641572),
        ("dargett-brewpub", 1528641034), ("dargett-brewpub", 1528638060),
    ]
    assert {(s.source, s.kind, s.brewery_id) for s in result.sightings} == {
        ("untappd_brewery", "checkin", 265165)}


def test_checkin_sighting_fields():
    page = brewery_page()
    result = fetch_brewery_checkins(FakeClient(page), DARGETT, CONFIG, NOW, {})
    # The shared check-in sightings, only tagged with the page's brewery id.
    shared = checkins_to_sightings(parse_checkins(page), CONFIG, "untappd_brewery", NOW, {})
    assert result.sightings == [replace(s, brewery_id=265165) for s in shared]
    by_id = {s.checkin_id: s for s in result.sightings}
    cherry = by_id[1528641034]
    assert (cherry.place_id, cherry.beer_key, cherry.untappd_beer_id, cherry.name, cherry.brewery) == (
        "dargett-brewpub", "u:1559917", 1559917, "Cherry Ale (Morello)", "Dargett Brewery")
    assert (cherry.serving, cherry.seen_at, cherry.url, cherry.at_home) == (
        None, datetime(2025, 11, 15, 15, 13, 4, tzinfo=timezone.utc),
        "https://untappd.com/b/dargett-brewery-cherry-ale-morello/1559917", False)
    assert (cherry.rating, cherry.style, cherry.abv) == (None, None, None)   # never a personal rating
    # serving is passed through as is: rules.py decides which servings count
    assert [(by_id[i].beer_key, by_id[i].serving) for i in (1528689976, 1528688333, 1528638060)] == [
        ("u:1570010", "Taster"), ("u:1570010", "Draft"), ("u:2301685", "Draft")]


def test_brewery_page_records_every_venue_seen_in_checkins():
    # 20 check-ins: 4 at-home and 2 without a venue are dropped; the rest include untracked venues.
    result = fetch_brewery_checkins(FakeClient(brewery_page()), DARGETT, CONFIG, NOW, {})
    assert len(result.venue_checkins) == 14
    assert all(isinstance(vc, VenueCheckin) for vc in result.venue_checkins)
    names = {vc.venue_name for vc in result.venue_checkins}
    assert names == {"Dargett Craft Brewery", "Number 8", "Ferment", "Caffe Napoli"}
    napoli = next(vc for vc in result.venue_checkins if vc.venue_name == "Caffe Napoli")
    assert napoli.venue_url == "https://untappd.com/v/caffe-napoli/645961"
    assert result.venue_meta is None   # a brewery page, not a venue page


def test_no_checkins_at_config_places_is_ok():
    tap_station = Place(id="tap-station", name="Tap Station", kind="bar",
                        sources={"untappd_checkins": {"slug": "tap-station", "venue_id": 8234456}})
    config = Config(places={"tap-station": tap_station}, breweries=(DARGETT,), settings=Settings())
    result = fetch_brewery_checkins(FakeClient(brewery_page()), DARGETT, config, NOW, {})
    assert (result.ok, result.error, result.sightings) == (True, None, [])


def test_page_without_checkins_is_empty():
    # Changed markup or a login wall: the brewery header is there, no check-in parses.
    page = brewery_page().replace('data-checkin-id="', 'data-old-id="')
    result = fetch_brewery_checkins(FakeClient(page), DARGETT, CONFIG, NOW, {})
    assert (result.key, result.source, result.ok, result.error, result.sightings) == (
        "untappd_brewery:265165", "untappd_brewery", False, "empty", [])


@pytest.mark.parametrize("kind", ["network", "cloudflare", "blocked", "budget", "http"])
def test_checkins_fetch_error(kind):
    result = fetch_brewery_checkins(FakeClient(error=FetchError(kind, DARGETT.url)), DARGETT, CONFIG, NOW, {})
    assert (result.key, result.source, result.ok, result.error, result.brewery_id, result.sightings) == (
        "untappd_brewery:265165", "untappd_brewery", False, kind, 265165, [])


# --- beer list (synthetic page) -----------------------------------------------------------
# No capture of the real list page (/w/<slug>/<id>/beer) exists yet. The header below is copied
# from the real brewery page (same "N Beers" counter); rows mirror Untappd's div.beer-item markup
# with real Dargett ids, names and styles from the page's "Top Beers" sidebar and made-up ABVs.
# parse_beer_list is checked against the live page at launch; list_enabled stays false until then.
HEADER = """
<div class="box b_info"><div class="content">
 <div class="top">
  <div class="basic">
   <div class="name">
    <h1>
						Dargett Brewery					</h1>
    <p class="brewery">
						Yerevan, Armenia					</p>
    <p class="style">Brew Pub</p>
   </div>
  </div>
  <div class="stats"><p><span class="stat">Total</span> <span class="count">28,799</span></p></div>
 </div>
 <div class="details brewery  claimed">
  <div class="caps" data-rating="3.51"></div><span class="num">(3.51)</span>
  <p class="raters">
				24,361 Ratings			</p><p class="count">
				<a href="/Dargett/beer">{total}</a>
			</p>
 </div>
</div></div>
"""
ROW = """
<div class="beer-item " data-bid="{bid}">
 <a class="label" href="/b/{slug}/{bid}">
  <img src="https://assets.untappd.com/site/beer_logos/beer-{bid}.jpeg" alt="{name} label"></a>
 <div class="beer-details">
  <p class="name"><a href="/b/{slug}/{bid}">{name}</a></p>
  <p class="style">{style}</p>
  <div class="desc desc-half-{bid}">Brewed in Yerevan.</div>
 </div>
 <div class="details beer">
  <div class="abv">
			{abv}
		</div>
  <div class="ibu">
			N/A IBU
		</div>
  <div class="rating"><div class="caps" data-rating="3.62"></div><span class="num">(3.62)</span></div>
  <div class="raters">512 Ratings</div>
 </div>
</div>
"""
ROWS = [
    dict(bid=1547626, slug="dargett-brewery-black-ipa-milestones", name="Black IPA (Milestones)",
         style="IPA - Black / Cascadian Dark Ale", abv="6.5% ABV"),
    dict(bid=2508041, slug="dargett-brewery-armenian-imperial-stout-brandy-barrel-aged",
         name="Armenian Imperial Stout (Brandy Barrel Aged)", style="Stout - Imperial / Double", abv="11% ABV"),
    dict(bid=1538345, slug="dargett-brewery-vienna-lager-metamorphosis", name="Vienna Lager (Metamorphosis)",
         style="Lager - Vienna", abv="N/A ABV"),
]


def list_page(total="3 Beers", header=HEADER):
    return ("<html><body>" + header.replace("{total}", total) + '<div class="beer-container beer-list pad">'
            + "".join(ROW.format(**r) for r in ROWS) + "</div></body></html>")


def test_parse_beer_list():
    assert parse_beer_list(list_page()) == BeerList(total=3, beers=[
        BreweryBeer(brewery_id=0, untappd_beer_id=1547626, name="Black IPA (Milestones)",
                    brewery="Dargett Brewery", style="IPA - Black / Cascadian Dark Ale", abv=6.5,
                    url="https://untappd.com/b/dargett-brewery-black-ipa-milestones/1547626"),
        BreweryBeer(brewery_id=0, untappd_beer_id=2508041, name="Armenian Imperial Stout (Brandy Barrel Aged)",
                    brewery="Dargett Brewery", style="Stout - Imperial / Double", abv=11.0,
                    url="https://untappd.com/b/dargett-brewery-armenian-imperial-stout-brandy-barrel-aged/2508041"),
        BreweryBeer(brewery_id=0, untappd_beer_id=1538345, name="Vienna Lager (Metamorphosis)",
                    brewery="Dargett Brewery", style="Lager - Vienna", abv=None,
                    url="https://untappd.com/b/dargett-brewery-vienna-lager-metamorphosis/1538345"),
    ])


@pytest.mark.parametrize("counter, total", [
    ("39 Beers", 39), ("1,204 Beers", 1204), ("1 Beer", 1), ("Beers", None), ("", None)])
def test_parse_total(counter, total):
    assert parse_beer_list(list_page(total=counter)).total == total


def test_parse_real_brewery_page_header():
    # The list page shares this header. The brewery page links beers in check-ins and in the
    # "Top Beers" sidebar, but those are not list rows.
    assert parse_beer_list(brewery_page()) == BeerList(total=39, beers=[])


def test_broken_rows_are_skipped_and_make_the_list_incomplete():
    page = (list_page()
            .replace('<a href="/b/dargett-brewery-black-ipa-milestones/1547626">Black IPA (Milestones)</a>',
                     '<a href="/b/dargett-brewery-black-ipa-milestones/1547626"> </a>')
            .replace('<a href="/b/dargett-brewery-vienna-lager-metamorphosis/1538345">',
                     '<a href="/beer/1538345">'))
    parsed = parse_beer_list(page)
    assert (parsed.total, [b.untappd_beer_id for b in parsed.beers]) == (3, [2508041])
    assert fetch_brewery_list(FakeClient(page), DARGETT, NOW).error == "incomplete"


# --- beer list fetch ----------------------------------------------------------------------

def test_fetch_brewery_list_complete():
    client = FakeClient(list_page())
    result = fetch_brewery_list(client, DARGETT, NOW)
    assert client.urls == [LIST_URL]
    assert (result.key, result.source, result.ok, result.error) == (
        "untappd_brewery_list:265165", "untappd_brewery_list", True, None)
    assert (result.brewery_id, result.place_id, result.sightings) == (265165, None, [])
    assert result.brewery_beers[0] == BreweryBeer(
        brewery_id=265165, untappd_beer_id=1547626, name="Black IPA (Milestones)", brewery="Dargett Brewery",
        style="IPA - Black / Cascadian Dark Ale", abv=6.5,
        url="https://untappd.com/b/dargett-brewery-black-ipa-milestones/1547626")
    assert [(b.brewery_id, b.untappd_beer_id, b.brewery) for b in result.brewery_beers] == [
        (265165, 1547626, "Dargett Brewery"), (265165, 2508041, "Dargett Brewery"),
        (265165, 1538345, "Dargett Brewery")]


def test_fetch_brewery_list_falls_back_to_config_name():
    result = fetch_brewery_list(FakeClient(list_page(header=HEADER.replace("Dargett Brewery", ""))), DARGETT, NOW)
    assert result.ok
    assert {b.brewery for b in result.brewery_beers} == {"Dargett"}


@pytest.mark.parametrize("counter", ["39 Beers", "4 Beers", "2 Beers", "Beers"])
def test_fetch_brewery_list_incomplete(counter):
    # "39 Beers" with 3 rows: only the first page of the list is visible without login
    result = fetch_brewery_list(FakeClient(list_page(total=counter)), DARGETT, NOW)
    assert (result.key, result.source, result.ok, result.error, result.brewery_id, result.brewery_beers) == (
        "untappd_brewery_list:265165", "untappd_brewery_list", False, "incomplete", 265165, [])


def test_fetch_brewery_list_on_brewery_page_is_incomplete():
    # If the list URL lands on the brewery page: counter 39, no list rows -> not an empty success
    result = fetch_brewery_list(FakeClient(brewery_page()), DARGETT, NOW)
    assert (result.ok, result.error, result.brewery_beers) == (False, "incomplete", [])


@pytest.mark.parametrize("kind", ["network", "cloudflare", "blocked", "budget", "http"])
def test_fetch_brewery_list_fetch_error(kind):
    result = fetch_brewery_list(FakeClient(error=FetchError(kind, LIST_URL)), DARGETT, NOW)
    assert (result.key, result.ok, result.error, result.brewery_id, result.brewery_beers) == (
        "untappd_brewery_list:265165", False, kind, 265165, [])
