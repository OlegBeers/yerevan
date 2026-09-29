"""site/stats.html run for real: Chrome driven by Playwright, the page and its stats.json served from
memory (no network). Skipped where neither the installed Chrome nor Playwright's Chromium can be launched."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PAGE = Path(__file__).resolve().parent.parent / "site" / "stats.html"
ORIGIN = "https://taps.test"
CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def make_stats(*, first_record_ago=timedelta(days=200)):
    """A stats.json with distinct numbers per period, so switching periods is visibly observable."""
    now = datetime.now(timezone.utc)

    def period(checkins, venue_checkins, venue_visitors):
        return {
            "summary": {"checkins": checkins, "people": 2, "venues": 1, "beers": 2, "breweries": 2,
                       "avg_rating": 4.25},
            "venues": [{"venue_id": 1, "name": "Dors Bar", "checkins": venue_checkins,
                       "unique_visitors": venue_visitors, "avg_rating": 4.25,
                       "top_beer": {"beer_id": 100, "name": "Cherry Ale"}}],
            "top_beers": [{"beer_id": 100, "name": "Cherry Ale", "brewery": "Dargett", "checkins": checkins},
                         {"beer_id": 200, "name": None, "brewery": "Gyumri", "checkins": 1}],
            "top_breweries": [{"brewery": "Dargett", "checkins": checkins}, {"brewery": "Gyumri", "checkins": 1}],
            "top_styles": [{"style": "IPA", "checkins": checkins}, {"style": None, "checkins": 1}],
            "brewery_origin": {"armenian": 1, "imported": 1, "unknown": 0},
            "new_beers": [{"beer_id": 200, "name": None, "brewery": "Gyumri"}],
            "new_breweries": [{"brewery": "Gyumri"}],
            "people": [
                {"rank": 1, "username": "annadrinks", "profile_url": "https://untappd.com/user/annadrinks",
                 "checkins": checkins, "unique_beers": 2, "venues": 1},
                {"rank": 2, "username": "bobbeer", "profile_url": "https://untappd.com/user/bobbeer",
                 "checkins": 1, "unique_beers": 1, "venues": 1},
            ],
        }

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "first_record_at": (now - first_record_ago).isoformat(timespec="seconds"),
        "periods": {"30d": period(3, 3, 2), "90d": period(5, 5, 2), "all": period(9, 9, 2)},
    }


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        for options in ({"channel": "chrome"}, {}):
            try:
                browser = playwright.chromium.launch(**options)
                break
            except sync_api.Error:
                continue
        else:
            pytest.skip("no Chrome or Chromium to run the page in")
        yield browser
        browser.close()


@pytest.fixture
def open_page(browser):
    contexts = []

    def open_page(data=None, *, locale="en-US", query="", width=1280):
        context = browser.new_context(locale=locale, viewport={"width": width, "height": 900})
        context.set_default_timeout(5000)
        contexts.append(context)
        page = context.new_page()
        problems = []
        page.on("pageerror", lambda error: problems.append(str(error)))
        page.on("console", lambda msg: problems.append(msg.text) if msg.type == "error" else None)

        def serve(route):
            url = urlsplit(route.request.url)
            if url.path.endswith("/stats.html"):
                route.fulfill(body=PAGE.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
            elif url.path.endswith("/stats.json"):
                route.fulfill(json=data if data is not None else make_stats())
            elif url.netloc.startswith("fonts."):
                route.fulfill(body="", content_type="text/css")
            else:
                route.abort()

        page.route("**/*", serve)
        page.goto(f"{ORIGIN}/stats.html{query}")
        page.wait_for_selector("#summary .stat-tile")
        return page, problems

    yield open_page
    for context in contexts:
        context.close()


def text(page, selector):
    return " ".join((page.text_content(selector) or "").split())


def tile_nums(page):
    return page.eval_on_selector_all("#summary .stat-tile .num", "els => els.map((e) => e.textContent)")


STRINGS_JS = """() => {
  const out = [document.title, document.querySelector('meta[name=description]').content];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode())
    if (!['SCRIPT', 'STYLE'].includes(n.parentElement.tagName) && n.textContent.trim()) out.push(n.textContent.trim());
  for (const el of document.querySelectorAll('[aria-label],[title]')) {
    if (el.closest('.lang button')) continue;
    for (const a of ['aria-label', 'title']) if (el.hasAttribute(a)) out.push(el.getAttribute(a));
  }
  return out;
}"""


def strings(page):
    return page.evaluate(STRINGS_JS)


def test_the_page_loads_showing_bars_and_the_30_day_period_by_default(open_page):
    page, problems = open_page()
    assert page.get_attribute("#tab-bars", "aria-pressed") == "true"
    assert page.get_attribute("#period-30d", "aria-pressed") == "true"
    assert tile_nums(page)[0] == "3"   # 30d summary.checkins from make_stats()
    assert text(page, "#bars-list .card .card-name") == "Dors Bar"
    assert problems == []


def test_switching_the_period_updates_the_summary_and_the_venue_card(open_page):
    page, problems = open_page()
    page.click("#period-90d")
    assert page.get_attribute("#period-90d", "aria-pressed") == "true"
    assert page.get_attribute("#period-30d", "aria-pressed") == "false"
    assert tile_nums(page)[0] == "5"
    page.click("#period-all")
    assert tile_nums(page)[0] == "9"
    assert problems == []


def test_switching_to_the_people_tab_shows_the_leaderboard(open_page):
    page, problems = open_page()
    page.click("#tab-people")
    assert page.get_attribute("#tab-people", "aria-pressed") == "true"
    assert page.is_hidden("#bars-section") and page.is_visible("#people-section")
    rows = page.locator("#people-list > li")
    assert rows.count() == 2
    first = rows.nth(0)
    assert first.locator(".lb-rank").inner_text() == "1"
    link = first.locator("a")
    assert link.inner_text() == "annadrinks"
    assert link.get_attribute("href") == "https://untappd.com/user/annadrinks"
    assert "3" in first.locator(".meta").inner_text()   # checkins for the default (30d) period
    assert problems == []


def test_switching_to_the_beer_tab_shows_top_beers_breweries_styles_and_origin(open_page):
    page, problems = open_page()
    page.click("#tab-beer")
    assert page.is_visible("#beer-section")
    assert "Cherry Ale" in text(page, "#top-beers")
    assert "Dargett" in text(page, "#top-breweries")
    assert "IPA" in text(page, "#top-styles")
    origin_nums = page.eval_on_selector_all("#brewery-origin .stat-tile .num", "els => els.map((e) => e.textContent)")
    assert origin_nums == ["1", "1", "0"]
    assert problems == []


def test_the_back_link_points_to_the_beer_list(open_page):
    page, problems = open_page()
    assert page.get_attribute("a.back-link", "href") == "index.html"
    assert problems == []


def test_the_language_switch_updates_tabs_periods_and_html_lang(open_page):
    page, problems = open_page(locale="ru-RU")
    assert page.get_attribute("html", "lang") == "ru"
    assert text(page, "#tab-bars") == "🍻 Бары"
    assert text(page, "#period-30d") == "30 дней"
    page.click(".lang [data-lang=en]")
    assert page.get_attribute("html", "lang") == "en"
    assert text(page, "#tab-bars") == "🍻 Bars"
    assert text(page, "#period-30d") == "30 days"
    assert problems == []


@pytest.mark.parametrize("width", [1280, 375])
def test_the_english_page_has_no_russian_left_on_any_tab(open_page, width):
    page, problems = open_page(width=width)
    for tab in ("tab-bars", "tab-beer", "tab-people"):
        page.click(f"#{tab}")
        left = [s for s in strings(page) if CYRILLIC.search(s)]
        assert left == [], (tab, left)
    assert problems == []


def test_the_footnote_names_the_first_record_date(open_page):
    page, problems = open_page(locale="ru-RU")
    assert "с" in text(page, "#footnote") and re.search(r"\d\d\.\d\d\.\d{4}", text(page, "#footnote"))
    assert problems == []


def test_the_people_tab_shows_the_opt_out_note(open_page):
    page, problems = open_page(locale="ru-RU")
    page.click("#tab-people")
    assert "@oleg_sorokin" in text(page, "#people-note")
    assert problems == []


