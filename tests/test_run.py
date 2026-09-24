"""Integration tests for one run: real sources and rules on fixtures, fake web, Telegram and git."""
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from taps import run as run_mod
from taps.config import ConfigError
from taps.fetch import FetchError, HttpResponse
from taps.gitsync import commit_and_push, pull_ff
from taps.model import SourceResult
from taps.rules import MergeOutcome
from taps.run import Deps, main, run, update_alerts
from taps.sources.parma import fetch_parma as real_fetch_parma
from taps.state import empty_state, load_state, save_state
from taps.telegram import Alerter, SendOutcome, send_message
from taps.timeutil import iso
from tests.helpers import fixture_json, fixture_text

ROOT = Path(__file__).parent.parent
NOW = datetime(2026, 9, 24, 14, 17, tzinfo=timezone.utc)       # Thu 18:17 in Yerevan
NEXT_EVENING = NOW + timedelta(days=1)
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
     "basePrice": 2500} for i, n in enumerate(BUYAM_NAMES)]}})
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

    def __init__(self, tmp_path, untappd=None, send=(), push=()):
        self.repo = tmp_path
        (tmp_path / "site").mkdir(exist_ok=True)
        (tmp_path / "places.yaml").write_text(PLACES_YAML, encoding="utf-8")
        shutil.copy(ROOT / "corrections.yaml", tmp_path / "corrections.yaml")
        self.untappd = untappd or FakeUntappd()
        self.http = FakeHttp()
        self.sends, self.pushes, self.pulls = [], [], []
        self.send_outcomes, self.push_results = list(send), list(push)

    def send(self, token, chat_id, text, button=None):
        self.sends.append({"token": token, "chat": chat_id, "text": text, "button": button})
        return self.send_outcomes.pop(0) if self.send_outcomes else SendOutcome("sent")

    def push(self, repo, paths, message):
        state = json.loads((repo / "state.json").read_text(encoding="utf-8"))
        self.pushes.append({"paths": list(paths), "state": state})
        return self.push_results.pop(0) if self.push_results else True

    def deps(self):
        return Deps(http=self.http, untappd_fetcher=self.untappd, send=self.send,
                    pull=self.pulls.append, push=self.push, sleep=lambda s: None)

    def run(self, now, **kw):
        return run(self.repo, now, ENV, self.deps(), **kw)

    def state(self):
        return load_state(self.repo / "state.json", NOW)

    def edit_state(self, change):
        state = self.state()
        change(state)
        save_state(self.repo / "state.json", state)

    def next_run(self, untappd=None, send=(), push=()):
        """Fresh fakes for the next run in the same repo."""
        self.untappd = untappd or FakeUntappd()
        self.http = FakeHttp()
        self.sends, self.pushes, self.pulls = [], [], []
        self.send_outcomes, self.push_results = list(send), list(push)


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
    # the mark was pushed before sending
    assert world.pushes[0]["state"]["digest"]["sent_count"] == 1
    assert len(world.pushes) == 1


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
    state.untappd.pages_today, state.untappd.pages_date = 29, "2026-09-24"   # 1 of 30 pages left
    save_state(world.repo / "state.json", state)
    assert world.run(NOW) == 0

    state = world.state()
    assert world.untappd.urls == [GARGOYLE]            # its second tab and everything after: no budget
    assert not any(k.startswith("untappd") for k in state.sources)
    assert state.untappd.pages_today == 30 and state.alerts == {} and world.sends == []


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
