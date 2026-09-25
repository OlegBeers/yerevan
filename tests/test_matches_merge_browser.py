"""The «Объединить пиво» mode of site/matches.html, run for real: Chrome driven by Playwright, the page and its data.json
served from memory (no network). Skipped where neither the installed Chrome nor Playwright's Chromium can be launched."""
import base64
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_site_i18n_browser import browser  # noqa: F401  (the shared fixture: a launched Chrome; the import skips this module without Playwright)

PAGE = Path(__file__).resolve().parent.parent / "site" / "matches.html"
ORIGIN = "https://taps.test"
PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
LOGO = "https://img.test/label.png"


def row(place_id, key, name, brewery=None, **kw):
    """A row of data.json as taps/site_data.py writes it, trimmed to the fields the page reads."""
    return {"place_id": place_id, "beer_key": key, "name": name, "brewery": brewery, "abv": None, "price_amd": None,
            "beer_logo": LOGO, "shop_name": None, "shop_brewery": None, "match_via": None, **kw}


WOLF_IPA = row("beer-city", "n:волковская пиваварня indian pale ale ipa", "Волковская пиваварня Indian Pale Ale Ipa",
               "Wolf's Brewery", abv=5.9, price_amd=810, shop_name="Волковская пиваварня Indian Pale Ale Ipa",
               shop_brewery="Wolf's Brewery", match_via="manual")
WOLF_IPA_LIGHT = row("parma", "n:wolfs ipa light", "Wolfs Brewery IPA light", "ЗАО МПК", abv=5.9, price_amd=830)
WOLF_APA_LIGHT = row("parma", "n:wolfs apa light", "Wolfs Brewery APA light", "ЗАО МПК", abv=5.5, price_amd=830)
OTHER_LAGER = row("yerevan-city", "n:other lager", "Other Lager", "Some Brewery", abv=4.8)
AT_A_BAR = row("gargoyle", "u:1561153", "Nepravilnyi Mead", "Wolf's Brewery (Волковская Пиваварня)")   # a bar's Untappd beer: no n: key


def make_data(*extra_rows):
    places = [{"id": id, "name": name} for id, name in (
        ("beer-city", "Beer City"), ("parma", "Parma"), ("yerevan-city", "Yerevan City"), ("gargoyle", "Gargoyle Bar"))]
    match = {"place_id": "beer-city", "place": "Beer City", "key": WOLF_IPA["beer_key"], "shop_name": WOLF_IPA["name"],
             "shop_brewery": "Wolf's Brewery", "shop_url": None, "shop_photo": None, "untappd_name": "Wolf IPA",
             "untappd_brewery": "Wolf's Brewery", "untappd_url": "https://untappd.com/beer/1941326", "untappd_logo": None,
             "rating": None, "via": "manual", "weak": False}
    return {"places": places, "matches": [match],
            "rows": [WOLF_IPA, WOLF_IPA_LIGHT, WOLF_APA_LIGHT, OTHER_LAGER, AT_A_BAR, *extra_rows]}


@pytest.fixture
def open_page(browser):
    """open_page(...) -> (page, problems): matches.html loaded with its data, and the list that collects script errors."""
    contexts = []

    def open_page(data=None, *, width=375, mode="review", dark=False):
        context = browser.new_context(viewport={"width": width, "height": 800}, color_scheme="dark" if dark else "light")
        context.set_default_timeout(5000)   # everything is served from memory: a broken page should fail fast
        contexts.append(context)
        page = context.new_page()
        problems = []
        page.on("pageerror", lambda error: problems.append(str(error)))
        page.on("console", lambda msg: problems.append(msg.text) if msg.type == "error" else None)

        def serve(route):
            url = urlsplit(route.request.url)
            if url.path.endswith("/matches.html"):
                route.fulfill(body=PAGE.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
            elif url.path.endswith("/data.json"):
                route.fulfill(json=data if data is not None else make_data())
            elif url.netloc.startswith("fonts."):
                route.fulfill(body="", content_type="text/css")
            elif url.netloc == "img.test":
                route.fulfill(body=PIXEL, content_type="image/png")
            else:
                route.abort()

        page.route("**/*", serve)
        page.goto(f"{ORIGIN}/matches.html")
        page.wait_for_selector("#list > *")
        if mode == "merge":
            page.click("#mode-merge")
        return page, problems

    yield open_page
    for context in contexts:
        context.close()


def test_the_merge_mode_takes_the_place_of_the_review_list_and_its_bottom_bar(open_page):
    page, problems = open_page()
    assert page.is_visible("#review") and page.is_visible("#list") and page.is_visible("#bar")
    assert not page.is_visible("#merge")
    assert [page.get_attribute(f"#mode-{m}", "aria-pressed") for m in ("review", "merge")] == ["true", "false"]
    page.click("#mode-merge")
    assert page.is_visible("#merge") and not page.is_visible("#review") and not page.is_visible("#bar")
    assert [page.get_attribute(f"#mode-{m}", "aria-pressed") for m in ("review", "merge")] == ["false", "true"]
    page.click("#mode-review")
    assert page.is_visible("#review") and page.is_visible("#list") and page.is_visible("#bar") and not page.is_visible("#merge")
    assert problems == []
