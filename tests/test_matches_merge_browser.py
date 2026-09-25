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

    def open_page(data=None, *, width=375, mode="review", dark=False, status=200):
        context = browser.new_context(viewport={"width": width, "height": 800}, color_scheme="dark" if dark else "light")
        context.set_default_timeout(5000)   # everything is served from memory: a broken page should fail fast
        contexts.append(context)
        page = context.new_page()
        problems = []
        page.on("pageerror", lambda error: problems.append(str(error)))
        page.on("console", lambda msg: problems.append(msg.text) if msg.type == "error" and status == 200 else None)

        def serve(route):
            url = urlsplit(route.request.url)
            if url.path.endswith("/matches.html"):
                route.fulfill(body=PAGE.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
            elif url.path.endswith("/data.json"):
                route.fulfill(status=status, json=data if data is not None else make_data())
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


def names(page):
    return page.eval_on_selector_all("#pick-list .pick .name", "els => els.map((e) => e.textContent)")


def text(page, selector):
    return " ".join((page.text_content(selector) or "").split())


def size(page, selector):
    box = page.locator(selector).first.bounding_box()
    return box["width"], box["height"]


def test_the_search_offers_shop_items_only_unlinked_ones_first_and_finds_by_name_brewery_or_place(open_page):
    page, problems = open_page(mode="merge")
    # the bar's Untappd beer has a u: key: same_as takes shop names only. An item already linked to Untappd stays pickable, last
    assert names(page) == ["Wolfs Brewery APA light", "Wolfs Brewery IPA light", "Other Lager", WOLF_IPA["name"]]
    assert text(page, "#pick-count") == "Позиций: 4"
    page.fill("#pick-search", "wolf")     # the brewery of the linked one, the shop's name of the others
    assert names(page) == ["Wolfs Brewery APA light", "Wolfs Brewery IPA light", WOLF_IPA["name"]]
    page.fill("#pick-search", "ВОЛК")     # another alphabet, another case
    assert names(page) == [WOLF_IPA["name"]]
    page.fill("#pick-search", " wolf   ipa ")   # every word must match, in any order
    assert names(page) == ["Wolfs Brewery IPA light", WOLF_IPA["name"]]
    page.fill("#pick-search", "yerevan")       # the place
    assert names(page) == ["Other Lager"]
    page.fill("#pick-search", "нет такого пива")
    assert names(page) == [] and text(page, "#pick-list") == "Ничего не нашлось — попробуйте другой запрос."
    assert text(page, "#pick-count") == "Позиций: 0"
    assert problems == []


def test_the_pair_key_is_searched_too_it_holds_the_shops_name_in_latin_letters(open_page):
    page, problems = open_page(make_data(row("yerevan-city", "n:volkovskaya pivovarnya apa g b", "Волк.пивоварня APA", "Пивоварня")),
                               mode="merge")
    page.fill("#pick-search", "volkovskaya apa")
    assert names(page) == ["Волк.пивоварня APA"]
    assert problems == []


def test_a_card_shows_the_photo_name_brewery_place_abv_price_and_whether_it_is_already_linked(open_page):
    page, problems = open_page(mode="merge")
    page.fill("#pick-search", "wolf ipa")
    light, linked = page.locator("#pick-list .pick").all()
    assert light.locator(".small").all_text_contents() == ["ЗАО МПК", "Parma · 5.9% · 830 ֏"]
    assert light.locator("img.thumb").get_attribute("src") == LOGO
    assert light.locator(".badge").count() == 0
    assert linked.locator(".small").all_text_contents() == ["Wolf's Brewery", "Beer City · 5.9% · 810 ֏"]
    assert linked.locator(".badge").all_text_contents() == ["уже склеено с Untappd"]
    assert problems == []


def test_tapping_cards_collects_them_in_the_sheet_which_can_take_them_back(open_page):
    page, problems = open_page(mode="merge")
    assert text(page, "#sheet") == "Выбрано: 0"
    page.fill("#pick-search", "wolf")
    page.click("#pick-list li:nth-child(1) .pick")
    page.click("#pick-list li:nth-child(2) .pick")
    assert [page.is_checked(f"#pick-list li:nth-child({n}) input") for n in (1, 2, 3)] == [True, True, False]
    assert page.eval_on_selector_all("#pick-list .pick", "els => els.map((e) => e.classList.contains('on'))") == [True, True, False]
    assert text(page, "#picked-count") == "2"
    assert page.eval_on_selector_all("#picked-list .chip", "els => els.map((e) => e.textContent)") == [
        "Wolfs Brewery APA lightParma✕", "Wolfs Brewery IPA lightParma✕"]
    page.fill("#pick-search", "other")            # what was picked stays picked while the search changes
    page.click("#pick-list .pick")
    assert text(page, "#picked-count") == "3"
    page.fill("#pick-search", "wolf")
    assert [page.is_checked(f"#pick-list li:nth-child({n}) input") for n in (1, 2, 3)] == [True, True, False]
    page.click("#picked-list li:nth-child(1) button")   # the remove button unticks the card too
    assert text(page, "#picked-count") == "2"
    assert [page.is_checked(f"#pick-list li:nth-child({n}) input") for n in (1, 2, 3)] == [False, True, False]
    assert page.get_attribute("#picked-list li:nth-child(1) button", "aria-label") == "Убрать: Wolfs Brewery IPA light"
    page.click("#pick-list li:nth-child(2) .pick")      # a second tap on a card takes it back
    assert text(page, "#picked-count") == "1" and page.locator("#picked-list .chip").count() == 1
    assert problems == []


def test_at_most_thirty_cards_are_drawn_and_the_count_says_how_many_there_are(open_page):
    page, problems = open_page(make_data(*[row("parma", f"n:bulk lager {n}", f"Bulk Lager {n}") for n in range(40)]),
                               mode="merge")
    assert page.locator("#pick-list .pick").count() == 30
    assert text(page, "#pick-count") == "Позиций: 44, показаны первые 30 — введите название, чтобы найти нужную"
    page.fill("#pick-search", "bulk lager")
    assert text(page, "#pick-count") == "Позиций: 40, показаны первые 30 — введите название, чтобы найти нужную"
    page.fill("#pick-search", "bulk lager 39")
    assert text(page, "#pick-count") == "Позиций: 1" and page.locator("#pick-list .pick").count() == 1
    assert problems == []


def test_everything_to_tap_is_at_least_44px_high_on_a_phone(open_page):
    page, problems = open_page(mode="merge")
    page.fill("#pick-search", "wolf")
    page.click("#pick-list .pick")
    for selector in ("#mode-review", "#mode-merge", "#pick-search", "#pick-list .pick"):
        assert size(page, selector)[1] >= 44, selector
    assert min(size(page, "#picked-list .chip button")) >= 44
    assert problems == []


def test_a_phone_needs_no_sideways_scrolling_even_with_names_that_never_break(open_page):
    long_name = "Оченьдлинноеназваниебезпробеловикакихлибоподсказок" * 3
    page, problems = open_page(make_data(row("parma", f"n:{long_name.lower()}", long_name, long_name)), mode="merge")
    page.fill("#pick-search", "оченьдлинное")
    page.click("#pick-list .pick")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert problems == []


def test_a_failed_load_is_said_in_both_panels(open_page):
    page, _ = open_page(status=500, mode="merge")
    assert text(page, "#pick-list") == "Не удалось загрузить данные. Обновите страницу через минуту."
    page.click("#mode-review")
    assert text(page, "#list") == "Не удалось загрузить данные. Обновите страницу через минуту."
