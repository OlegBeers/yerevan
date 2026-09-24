from datetime import datetime, timezone

from bs4 import BeautifulSoup

from taps.config import Config, Place, Settings
from taps.fetch import FetchError
from taps.model import VenueCheckin
from taps.sources.untappd_checkins import (
    Checkin, checkins_to_sightings, checkins_to_venue_checkins, fetch_venue_checkins, parse_checkins,
    parse_venue_meta,
)
from tests.helpers import fixture_text

NOW = datetime(2024, 11, 1, 10, 0, tzinfo=timezone.utc)
CRAFT_STORY_URL = "https://untappd.com/v/craft-story/12281551"


def place(pid, venue_id, source="untappd_checkins"):
    return Place(id=pid, name=pid.title(), kind="bar", sources={source: {"slug": pid, "venue_id": venue_id}})


CRAFT_STORY = place("craft-story", 12281551)
CONFIG = Config(
    places={p.id: p for p in (CRAFT_STORY, place("vertigo", 11856429))},
    breweries=(),
    settings=Settings(),
)

# Real markup from dargett_brewery.html; the purchase venue differs from the check-in venue.
AT_HOME_HTML = """
<div class="item" id="checkin_1528967315" data-checkin-id="1528967315"><div class="checkin"><div class="top">
<p class="text"><a href="/user/user1" class="user">User</a> is drinking a
<a href="/b/dargett-brewery-pilsner-la-rapsodia/1518439">Pilsner (La Rapsodia)</a> by
<a href="/Dargett">Dargett Brewery</a> at <a href="/v/at-home/9917985">Untappd at Home</a></p>
<div class="checkin-comment"><p class="purchased">Purchased at <a href="/v/total-wine-more/115678">Total Wine</a></p>
<div class="rating-serving"><p class="serving"><img alt="Bottle"><span>Bottle</span></p></div></div>
</div><div class="feedback"><div class="bottom">
<a href="/user/user1/checkin/1528967315" class="time timezoner">Sun, 16 Nov 2025 00:44:33 +0000</a>
</div></div></div></div>
"""


class FakeClient:
    def __init__(self, html=None, error=None):
        self.html, self.error, self.urls = html, error, []

    def get(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.html


def by_id(checkins):
    return {c.checkin_id: c for c in checkins}


def test_craft_story_count_and_order():
    checkins = parse_checkins(fixture_text("untappd/craftstory_checkins.html"))
    assert len(checkins) == 20                 # 31 data-checkin-id attributes, 20 div.item blocks
    assert len({c.checkin_id for c in checkins}) == 20
    assert (checkins[0].checkin_id, checkins[-1].checkin_id) == (1429774602, 1418981193)
    assert {c.venue_id for c in checkins} == {12281551}


def test_craft_story_checkin_fields():
    c = by_id(parse_checkins(fixture_text("untappd/craftstory_checkins.html")))[1429774602]
    assert c == Checkin(
        checkin_id=1429774602, beer_id=5698328, beer_name="Black Is Beautiful Volume 2",
        brewery="Omnipollo", venue_id=12281551, venue_name="Craft Story", serving="Can",
        at_home=False, created_at=datetime(2024, 10, 31, 15, 50, 9, tzinfo=timezone.utc),
        venue_url="https://untappd.com/v/craft-story/12281551",
    )


def test_craft_story_servings_and_brewery_links():
    checkins = by_id(parse_checkins(fixture_text("untappd/craftstory_checkins.html")))
    assert checkins[1426811517].serving == "Draft"
    assert checkins[1419725361].serving == "Bottle"
    assert checkins[1426387350].serving is None           # no p.serving in this check-in
    assert checkins[1426387350].brewery == "Brauhaus Bevog"
    assert checkins[1419750295].brewery == "BlakStoc"     # brewery link in /w/<slug>/<id> form
    assert checkins[1426799004].beer_name == "NORTHERN STAR™ // CHOCOLATE, CARAMEL & BISCUIT PORTER"
    assert checkins[1419438214].brewery == "Steppe & Wind Meadery (Степь и Ветер)"


def test_vertigo_markup():
    # Items are "item " / "item fade-out-gradient" with an extra inner div; same fields otherwise.
    checkins = parse_checkins(fixture_text("untappd/vertigo_checkins.html"))
    assert [c.checkin_id for c in checkins] == [1515988927, 1505021426, 1505016023, 1502696702, 1497179096]
    first = checkins[0]
    assert (first.beer_id, first.beer_name, first.brewery) == (326032, "O'Hara's Double IPA", "O'Hara's Brewery")
    assert (first.venue_id, first.venue_name, first.serving) == (11856429, "Vertigo Bar & Bottleshop", None)
    assert first.created_at == datetime(2025, 9, 24, 20, 46, 45, tzinfo=timezone.utc)
    assert (checkins[-1].beer_name, checkins[-1].serving) == ("Leshy / Леший", "Can")


def test_vertigo_compact_list_adds_nothing():
    # The left-column div.venue-activity list repeats the stream's check-ins without venue and serving.
    html = fixture_text("untappd/vertigo_checkins.html")
    soup = BeautifulSoup(html, "html.parser")
    compact = [int(a["href"].rsplit("/", 1)[1]) for a in soup.select("div.venue-activity span.time a")]
    assert compact == [c.checkin_id for c in parse_checkins(html)]


def test_at_home_and_purchase_venue():
    [c] = parse_checkins(AT_HOME_HTML)
    assert c.at_home is True
    assert (c.venue_id, c.venue_name, c.beer_id, c.serving) == (9917985, "Untappd at Home", 1518439, "Bottle")


def test_brewery_page_at_home_and_no_venue():
    checkins = by_id(parse_checkins(fixture_text("untappd/dargett_brewery.html")))
    assert len(checkins) == 20
    assert (checkins[1528967315].at_home, checkins[1528967315].venue_id) == (True, 9917985)
    assert (checkins[1528921986].at_home, checkins[1528921986].venue_id) == (False, 645961)
    no_venue = checkins[1528687768]
    assert (no_venue.venue_id, no_venue.venue_name, no_venue.at_home) == (None, None, False)
    assert checkins[1528689976].serving == "Taster"


def test_browser_rewritten_time():
    # In a browser, refreshTime(".timezoner", "D MMM YY") turns the time into a date only.
    html = AT_HOME_HTML.replace("Sun, 16 Nov 2025 00:44:33 +0000", "6 Nov 25")
    [c] = parse_checkins(html)
    assert c.created_at == datetime(2025, 11, 6, tzinfo=timezone.utc)


def test_skips_duplicates_and_broken_items():
    no_beer = '<div class="item" data-checkin-id="5"><p class="text">no links</p><a class="time">6 Nov 25</a></div>'
    bad_time = AT_HOME_HTML.replace("1528967315", "6").replace("Sun, 16 Nov 2025 00:44:33 +0000", "2 hours ago")
    no_time = AT_HOME_HTML.replace("1528967315", "7").replace('class="time timezoner"', 'class="other"')
    html = AT_HOME_HTML + AT_HOME_HTML + no_beer + bad_time + no_time
    assert [c.checkin_id for c in parse_checkins(html)] == [1528967315]
    assert parse_checkins("<html><body>Nothing here</body></html>") == []


def test_checkins_to_sightings():
    checkins = parse_checkins(fixture_text("untappd/craftstory_checkins.html"))
    checkins += parse_checkins(fixture_text("untappd/vertigo_checkins.html"))
    checkins += parse_checkins(AT_HOME_HTML)
    sightings = checkins_to_sightings(checkins, CONFIG, "untappd_checkins", NOW, {})
    assert len(sightings) == 25                                   # at-home venue is not in config
    assert {s.place_id for s in sightings} == {"craft-story", "vertigo"}
    s = next(s for s in sightings if s.checkin_id == 1429774602)
    assert (s.place_id, s.source, s.kind, s.beer_key) == ("craft-story", "untappd_checkins", "checkin", "u:5698328")
    assert (s.name, s.title, s.brewery, s.untappd_beer_id) == (
        "Black Is Beautiful Volume 2", "Omnipollo Black Is Beautiful Volume 2", "Omnipollo", 5698328)
    assert s.seen_at == datetime(2024, 10, 31, 15, 50, 9, tzinfo=timezone.utc)   # check-in time, not now
    assert (s.serving, s.at_home, s.url) == ("Can", False, "https://untappd.com/beer/5698328")
    assert (s.rating, s.style, s.abv, s.brewery_id) == (None, None, None, None)  # personal ratings never used


def test_checkins_to_sightings_source_and_at_home_passthrough():
    home = place("home", 9917985)
    config = Config(places={"home": home}, breweries=(), settings=Settings())
    no_venue = Checkin(1, 2, "X", "Y", None, None, None, False, NOW)
    [s] = checkins_to_sightings(parse_checkins(AT_HOME_HTML) + [no_venue], config, "untappd_brewery", NOW, {})
    assert (s.source, s.kind, s.at_home, s.place_id, s.serving) == ("untappd_brewery", "checkin", True, "home", "Bottle")


def test_checkins_to_sightings_maps_venue_of_menu_place():
    # Brewery-page check-ins at a place with a menu still map; rules drop them later.
    config = Config(places={"cs": place("cs", 12281551, "untappd_menu")}, breweries=(), settings=Settings())
    sightings = checkins_to_sightings(
        parse_checkins(fixture_text("untappd/craftstory_checkins.html")), config, "untappd_brewery", NOW, {})
    assert len(sightings) == 20 and {s.place_id for s in sightings} == {"cs"}


def test_fetch_ok():
    client = FakeClient(fixture_text("untappd/craftstory_checkins.html"))
    result = fetch_venue_checkins(client, CRAFT_STORY, CONFIG, NOW, {})
    assert client.urls == [CRAFT_STORY_URL]
    assert (result.key, result.source, result.ok, result.error, result.place_id) == (
        "untappd_checkins:craft-story", "untappd_checkins", True, None, "craft-story")
    assert len(result.sightings) == 20
    assert {s.place_id for s in result.sightings} == {"craft-story"}
    assert result.sightings[0].checkin_id == 1429774602
    assert result.venue_meta == {
        "venue_id": 12281551, "name": "Craft-Story", "url": CRAFT_STORY_URL,
        "logo": "https://ss3.4sqi.net/img/categories_v2/nightlife/pub_bg_512.png", "verified": False,
    }
    assert len(result.venue_checkins) == 20
    assert all(vc.venue_id == 12281551 for vc in result.venue_checkins)


def test_fetch_error():
    for kind in ("cloudflare", "network", "blocked"):
        result = fetch_venue_checkins(FakeClient(error=FetchError(kind, CRAFT_STORY_URL)), CRAFT_STORY, CONFIG, NOW, {})
        assert (result.key, result.ok, result.error, result.sightings) == (
            "untappd_checkins:craft-story", False, kind, [])


def test_fetch_empty_page():
    result = fetch_venue_checkins(FakeClient("<html><body></body></html>"), CRAFT_STORY, CONFIG, NOW, {})
    assert (result.key, result.source, result.ok, result.error, result.sightings) == (
        "untappd_checkins:craft-story", "untappd_checkins", False, "empty", [])
    assert result.venue_meta is None and result.venue_checkins == []


def test_checkins_to_venue_checkins():
    checkins = parse_checkins(fixture_text("untappd/craftstory_checkins.html"))
    checkins += parse_checkins(AT_HOME_HTML)   # at-home checkin must be dropped
    checkins += [Checkin(1, 2, "X", "Y", None, None, None, False, NOW)]   # no venue must be dropped
    vcs = checkins_to_venue_checkins(checkins)
    assert len(vcs) == 20
    assert all(isinstance(vc, VenueCheckin) for vc in vcs)
    vc = next(vc for vc in vcs if vc.checkin_id == 1429774602)
    assert (vc.venue_id, vc.venue_name, vc.venue_url, vc.at) == (
        12281551, "Craft Story", "https://untappd.com/v/craft-story/12281551",
        datetime(2024, 10, 31, 15, 50, 9, tzinfo=timezone.utc))


def test_parse_venue_meta_verified_with_photo_logo():
    meta = parse_venue_meta(fixture_text("untappd/vertigo_checkins.html"))
    assert meta == {
        "logo": "https://assets.untappd.com/venuelogos/venue_11856429_fb656e75_bg_176.png?v=1",
        "verified": True,
    }


def test_parse_venue_meta_unverified():
    meta = parse_venue_meta(fixture_text("untappd/craftstory_checkins.html"))
    assert meta["verified"] is False
    assert meta["logo"] == "https://ss3.4sqi.net/img/categories_v2/nightlife/pub_bg_512.png"


def test_parse_venue_meta_missing_header_is_none():
    assert parse_venue_meta("<html><body>Nothing</body></html>") is None


def test_fetch_other_venue_is_bad_response():
    # All check-ins name another venue id (e.g. Untappd merged the venue): fail loudly, not "ok with 0".
    moved = place("craft-story", 99)
    config = Config(places={"craft-story": moved}, breweries=(), settings=Settings())
    result = fetch_venue_checkins(FakeClient(fixture_text("untappd/craftstory_checkins.html")), moved, config, NOW, {})
    assert (result.ok, result.error, result.sightings) == (False, "bad_response", [])
