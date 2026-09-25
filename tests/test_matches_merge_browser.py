"""The «Объединить пиво» mode of site/matches.html, run for real: Chrome driven by Playwright, the page and its data.json
served from memory (no network). Skipped where neither the installed Chrome nor Playwright's Chromium can be launched."""
import base64
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from taps.corrections import parse_corrections
from tests.test_site_i18n_browser import browser  # noqa: F401  (the shared fixture: a launched Chrome; the import skips this module without Playwright)

PAGE = Path(__file__).resolve().parent.parent / "site" / "matches.html"
ORIGIN = "https://taps.test"
PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
LOGO = "https://img.test/label.png"
PLACE_IDS = {"beer-city", "parma", "yerevan-city", "gargoyle"}
BEER_LINK = "https://untappd.com/b/wolf-s-brewery-indian-pale-ale-ipa/1941326"
NO_STORAGE = "Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('blocked', 'SecurityError'); } });"
NO_CLIPBOARD_API = """
Object.defineProperty(navigator, 'clipboard', { value: undefined });
document.addEventListener('copy', () => { const a = document.activeElement; window.copied = a.value.slice(a.selectionStart, a.selectionEnd); });
"""


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


# names and keys that would break a careless YAML block or the page's markup
AWKWARD_ROWS = [
    row("beer-city", "n:l'chaim: \"ipa\" \\ x # y", "L'Chaim: \"IPA\" \\ x # y", "Q"),
    row("parma", "n:" + "очень" * 25, "Очень" * 25, "Пивоварня"),
    row("parma", "n:<b>x</b> {a: [b]} - c", "<b>Bold</b> <img src=x onerror=alert(1)>", "<i>i</i>"),
]


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

    def open_page(data=None, *, width=375, mode="review", dark=False, status=200, init_script=None):
        context = browser.new_context(viewport={"width": width, "height": 800}, color_scheme="dark" if dark else "light")
        context.set_default_timeout(5000)   # everything is served from memory: a broken page should fail fast
        contexts.append(context)
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=ORIGIN)
        if init_script:
            context.add_init_script(init_script)
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


def test_a_linked_item_is_shown_and_found_by_its_shops_own_texts_not_by_the_untappd_beers(open_page):
    # r.name of a linked row is the Untappd beer's, the shop's own text is shop_name/shop_brewery
    linked = row("beer-city", "n:dargett weizen", "Weizen (Steppenwolf)", "Dargett Brewery", shop_name="Dargett weizen",
                 shop_brewery="Dargett", match_via="local")
    page, problems = open_page(make_data(linked), mode="merge")
    page.fill("#pick-search", "steppenwolf")
    assert names(page) == []
    page.fill("#pick-search", "dargett")
    assert names(page) == ["Dargett weizen"]
    assert page.locator("#pick-list .pick .small").all_text_contents() == ["Dargett", "Beer City"]
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
    assert text(page, "#sheet") == "Выбрано: 0 Сбросить Дальше ↓"
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
    for selector in ("#mode-review", "#mode-merge", "#pick-search", "#pick-list .pick", "#untappd-link", "#reset-merge", "#to-link"):
        assert size(page, selector)[1] >= 44, selector
    assert min(size(page, "#picked-list .chip button")) >= 44
    assert problems == []


def test_a_chip_in_the_sheet_keeps_to_one_line_of_name_however_long_it_is(open_page):
    long_name = "Волковская пиваварня Indian Pale Ale Ipa и ещё много слов после названия, которые не влезут в строку"
    page, problems = open_page(make_data(row("parma", "n:long", long_name, "B")), mode="merge")
    page.fill("#pick-search", "волковская пиваварня indian pale ale ipa и ещё")
    page.click("#pick-list .pick")
    assert 48 <= size(page, "#picked-list .chip")[1] <= 56     # a few picked items must not fill a phone's screen
    assert long_name in page.text_content("#picked-list .chip .name")   # cut by the eye, not in the text (a screen reader reads it all)
    assert problems == []


def test_a_phone_needs_no_sideways_scrolling_even_with_names_that_never_break(open_page):
    long_name = "Оченьдлинноеназваниебезпробеловикакихлибоподсказок" * 3
    page, problems = open_page(make_data(row("parma", f"n:{long_name.lower()}", long_name, long_name)), mode="merge")
    page.fill("#pick-search", "оченьдлинное")
    page.click("#pick-list .pick")
    page.fill("#untappd-link", BEER_LINK)
    assert page.is_visible("#yaml-out") and long_name.lower() in page.text_content("#yaml-out")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert problems == []


BEER_LINKS = [   # what Oleg may paste -> the number of the beer
    ("https://untappd.com/b/wolf-s-brewery-volkovskaya-pivovarnya-indian-pale-ale-ipa/1941326", "1941326"),
    ("untappd.com/beer/1941326", "1941326"),
    ("https://untappd.com/beer/1941326?ref=share#reviews", "1941326"),
    ("https://untappd.com/b/slug/1941326/", "1941326"),
    ("https://m.untappd.com/b/slug/1941326", "1941326"),
    ("Https://Untappd.com/B/Slug/1941326", "1941326"),                    # a phone's keyboard may capitalise
    ("1941326", "1941326"),
    ("  1941326 \n", "1941326"),
    ("Смотри: https://untappd.com/b/x/123 — это оно", "123"),             # a link inside a message
    ("https://untappd.com/b/x/123 https://untappd.com/beer/123", "123"),   # the same beer twice is one beer
    ("999999999", "999999999"),
]
NOT_BEER_LINKS = [
    "https://untappd.com/brewery/1234", "https://untappd.com/user/oleg/checkin/1234", "https://untappd.com/b/slug",
    "https://untappd.com/b/slug/abc", "https://untappd.com/beer/", "https://untp.beer/abc", "https://example.com/b/slug/1941326",
    "0", "007", "12.5", "-5", "1941326abc", "https://untappd.com/beer/0123", "1234567890", "untappd", "https://untappd.com/b/x/",
]


@pytest.mark.parametrize("pasted, number", BEER_LINKS)
def test_the_number_of_the_beer_is_read_from_its_untappd_link_or_from_the_bare_number(open_page, pasted, number):
    page, problems = open_page(mode="merge")
    assert page.evaluate("(t) => parseBeerLink(t)", pasted) == {"id": number}
    assert problems == []


@pytest.mark.parametrize("pasted", NOT_BEER_LINKS)
def test_anything_else_is_refused_not_guessed_at(open_page, pasted):
    page, problems = open_page(mode="merge")
    error = page.evaluate("(t) => parseBeerLink(t)", pasted)
    assert list(error) == ["error"] and error["error"].startswith("Не нашёл номер пива."), pasted
    assert problems == []


def test_two_different_beers_in_the_field_are_refused_rather_than_the_first_taken(open_page):
    page, _ = open_page(mode="merge")
    assert page.evaluate("(t) => parseBeerLink(t)", "https://untappd.com/b/a/111 https://untappd.com/b/b/222") == {
        "error": "В поле несколько разных ссылок — оставьте одну."}
    assert page.evaluate("parseBeerLink('')") is None and page.evaluate("parseBeerLink('  ')") is None


def test_the_field_says_what_it_understood_and_links_to_the_beer(open_page):
    page, problems = open_page(mode="merge")
    field = page.locator("#untappd-link")
    assert [field.get_attribute(a) for a in ("type", "inputmode", "autocomplete")] == ["url", "url", "off"]
    assert text(page, "label[for=untappd-link]") == "Ссылка на пиво в Untappd" and text(page, "#link-status") == ""
    field.fill("https://untappd.com/brewery/1234")
    assert text(page, "#link-status").startswith("Не нашёл номер пива.")
    assert field.get_attribute("aria-invalid") == "true" and page.locator("#link-status.bad").count() == 1
    field.fill("https://untappd.com/b/wolf-ipa/1941326?ref=x")
    assert text(page, "#link-status") == "Пиво № 1941326 · открыть на Untappd"
    link = page.locator("#link-status a")
    assert link.get_attribute("href") == "https://untappd.com/beer/1941326"
    assert link.get_attribute("target") == "_blank" and link.get_attribute("rel") == "noopener noreferrer"
    assert field.get_attribute("aria-invalid") == "false" and page.locator("#link-status.bad").count() == 0
    field.fill("")
    assert text(page, "#link-status") == "" and field.get_attribute("aria-invalid") == "false"
    assert problems == []


def test_the_button_of_the_sheet_takes_you_from_the_cards_to_the_link_field(open_page):
    page, problems = open_page(make_data(*[row("parma", f"n:bulk lager {n}", f"Bulk Lager {n}") for n in range(40)]), mode="merge")
    page.click("#pick-list li:nth-child(1) .pick")
    assert page.evaluate("document.activeElement.id") != "untappd-link"
    page.click("#to-link")
    assert page.evaluate("document.activeElement.id") == "untappd-link"
    assert page.eval_on_selector("#untappd-link", "(e) => e.getBoundingClientRect().top >= 0 && "
                                                  "e.getBoundingClientRect().bottom <= window.innerHeight")
    assert problems == []


def test_a_failed_load_is_said_in_both_panels(open_page):
    page, _ = open_page(status=500, mode="merge")
    assert text(page, "#pick-list") == "Не удалось загрузить данные. Обновите страницу через минуту."
    page.click("#mode-review")
    assert text(page, "#list") == "Не удалось загрузить данные. Обновите страницу через минуту."


def ready(page, link=BEER_LINK):
    """Two items picked (the unlinked Parma one first) and the beer's link typed in."""
    page.fill("#pick-search", "wolf ipa")
    page.click("#pick-list li:nth-child(1) .pick")
    page.click("#pick-list li:nth-child(2) .pick")
    page.fill("#untappd-link", link)


def copied_status(page):
    page.wait_for_function("document.getElementById('merge-status').textContent !== ''")
    return text(page, "#merge-status")


AWKWARD_TEXTS = [
    "n:волковская пиваварня indian pale ale ipa", "n:l'chaim: \"ipa\" \\ x # y", "n:a\\\"b", "n:\\", "n:\"", "n:'",
    "n:- not a list: [x, {y}] & *z !t %p @q `r | > ? # c", "  padded  ", "tab\there", "line\nbreak", "cr\r\nlf",
    "n:\u0085\u2028\u2029 separators", "n:🍺 emoji ёж ß Ω", "n:null", "n:yes", "n:123", "n:~", "true", "", "\x7f\x1b",
]


@pytest.mark.parametrize("value", AWKWARD_TEXTS)
def test_a_value_is_written_as_a_double_quoted_yaml_string_that_reads_back_as_it_was(open_page, value):
    page, problems = open_page(mode="merge")
    quoted = page.evaluate("(v) => yamlQuote(v)", value)
    assert quoted.startswith('"') and quoted.endswith('"') and "\n" not in quoted
    assert yaml.safe_load(f"key: {quoted}") == {"key": value}
    assert problems == []


def test_nothing_is_offered_to_copy_until_there_are_items_and_a_beer(open_page):
    page, problems = open_page(mode="merge")
    assert text(page, "#output-hint") == "Отметьте хотя бы одну позицию (шаг 1)." and not page.is_visible("#output")
    page.fill("#untappd-link", BEER_LINK)      # a beer, no items
    assert text(page, "#output-hint") == "Отметьте хотя бы одну позицию (шаг 1)." and not page.is_visible("#output")
    page.click("#pick-list .pick")
    assert not page.is_visible("#output-hint") and page.is_visible("#output")
    page.fill("#untappd-link", "https://untappd.com/brewery/1")    # items, a link that is no beer
    assert text(page, "#output-hint") == "Вставьте ссылку на пиво (шаг 2)." and not page.is_visible("#output")
    page.fill("#untappd-link", "")
    assert text(page, "#output-hint") == "Вставьте ссылку на пиво (шаг 2)." and not page.is_visible("#output")
    page.fill("#untappd-link", "1941326")
    assert page.is_visible("#output")
    page.click("#picked-list .chip-x")                                # the last item taken back
    assert text(page, "#output-hint") == "Отметьте хотя бы одну позицию (шаг 1)." and not page.is_visible("#output")
    assert problems == []


def test_one_yaml_entry_and_one_line_of_text_per_item_in_the_order_they_were_picked(open_page):
    page, problems = open_page(mode="merge")
    ready(page)
    assert page.text_content("#yaml-out") == (
        '  - place: "parma"\n'
        '    beer: "n:wolfs ipa light"\n'
        '    untappd_id: 1941326\n'
        '  - place: "beer-city"\n'
        '    beer: "n:волковская пиваварня indian pale ale ipa"\n'
        '    untappd_id: 1941326')
    assert page.text_content("#text-out") == (
        "parma | n:wolfs ipa light | Wolfs Brewery IPA light → https://untappd.com/beer/1941326\n"
        "beer-city | n:волковская пиваварня indian pale ale ipa | Волковская пиваварня Indian Pale Ale Ipa"
        " → https://untappd.com/beer/1941326")
    page.click("#picked-list li:nth-child(1) .chip-x")                  # taking one back, or changing the beer, rewrites both
    page.fill("#untappd-link", "untappd.com/beer/777")
    assert page.text_content("#yaml-out") == (
        '  - place: "beer-city"\n'
        '    beer: "n:волковская пиваварня indian pale ale ipa"\n'
        '    untappd_id: 777')
    assert page.text_content("#text-out").endswith(" → https://untappd.com/beer/777") and "\n" not in page.text_content("#text-out")
    assert problems == []


def test_the_block_is_read_by_the_bot_as_same_as_entries_even_for_awkward_names_and_keys(open_page):
    data = make_data(*AWKWARD_ROWS)
    page, problems = open_page(data, mode="merge")
    for card in page.locator("#pick-list .pick").all():
        card.click()
    assert page.locator("#picked-list .chip").count() == 7
    page.fill("#untappd-link", BEER_LINK)
    raw = yaml.safe_load("same_as:\n" + page.text_content("#yaml-out"))     # what corrections.yaml becomes once it is pasted under same_as:
    corrections, errors = parse_corrections(raw, PLACE_IDS)
    shop_rows = [r for r in data["rows"] if r["beer_key"].startswith("n:")]
    assert errors == [] and len(shop_rows) == 7
    assert corrections.same_as == {(r["place_id"], r["beer_key"]): 1941326 for r in shop_rows}
    lines = page.text_content("#text-out").split("\n")
    assert len(lines) == 7 and all(line.endswith(" → https://untappd.com/beer/1941326") for line in lines)
    assert problems == []


def test_names_are_shown_as_text_never_as_markup(open_page):
    page, problems = open_page(make_data(*AWKWARD_ROWS), mode="merge")
    page.fill("#pick-search", "bold")
    page.click("#pick-list .pick")
    page.fill("#untappd-link", BEER_LINK)
    assert page.locator("#merge b:not(#picked-count), #merge i, #merge img[onerror]").count() == 0
    assert "<b>Bold</b> <img src=x onerror=alert(1)>" in page.text_content("#picked-list")
    assert "<b>Bold</b> <img src=x onerror=alert(1)>" in page.text_content("#text-out")
    assert problems == []


def test_each_block_has_its_own_copy_button_and_says_what_was_copied(open_page):
    page, problems = open_page(mode="merge")
    ready(page)
    assert text(page, "#copy-yaml") == "Скопировать для corrections.yaml" and text(page, "#copy-text") == "Скопировать для Олега/Claude"
    page.click("#copy-yaml")
    assert copied_status(page) == "Скопировано — вставьте под same_as: в corrections.yaml"
    assert page.evaluate("navigator.clipboard.readText()") == page.text_content("#yaml-out")
    page.click("#pick-list li:nth-child(1) .pick")                      # what is on screen changed: the old note goes
    assert text(page, "#merge-status") == ""
    page.click("#copy-text")
    assert copied_status(page) == "Скопировано — отправьте Олегу или Claude"
    assert page.evaluate("navigator.clipboard.readText()") == page.text_content("#text-out")
    assert min(size(page, "#copy-yaml")[1], size(page, "#copy-text")[1]) >= 44
    assert problems == []


def test_without_the_clipboard_api_a_hidden_textarea_does_the_copying(open_page):
    page, problems = open_page(mode="merge", init_script=NO_CLIPBOARD_API)
    ready(page)
    page.click("#copy-text")
    assert copied_status(page) == "Скопировано — отправьте Олегу или Claude"
    assert page.evaluate("window.copied") == page.text_content("#text-out")
    assert page.locator("textarea").count() == 0                        # the helper cleans up after itself
    page.evaluate("document.execCommand = () => false")                # and when even that is refused, the page says so
    page.click("#copy-yaml")
    page.wait_for_function("document.getElementById('merge-status').textContent.startsWith('Не')")
    assert text(page, "#merge-status") == "Не удалось скопировать"
    assert problems == []


def test_the_review_lists_copy_button_still_puts_the_marked_lines_on_the_clipboard(open_page):
    page, problems = open_page()
    page.click("#list .verdict .bad input")
    page.click("#copy")
    page.wait_for_function("document.getElementById('copy-status').textContent !== ''")
    assert text(page, "#copy-status") == "Скопировано — отправьте Олегу"
    assert page.evaluate("navigator.clipboard.readText()") == (
        "beer-city | n:волковская пиваварня indian pale ale ipa | Волковская пиваварня Indian Pale Ale Ipa"
        " → Wolf IPA (https://untappd.com/beer/1941326)")
    assert problems == []


def test_reset_starts_over_but_leaves_the_review_marks_alone(open_page):
    page, problems = open_page()
    page.click("#list .verdict .bad input")                                    # a mark of the review list
    page.click("#mode-merge")
    page.fill("#pick-search", "wolf")
    page.click("#pick-list .pick")
    page.fill("#untappd-link", BEER_LINK)
    assert page.is_visible("#output")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.click("#reset-merge")
    assert page.input_value("#pick-search") == "" and page.input_value("#untappd-link") == ""
    assert text(page, "#picked-count") == "0" and page.locator("#picked-list .chip").count() == 0
    assert text(page, "#pick-count") == "Позиций: 4" and page.locator("#pick-list .pick input:checked").count() == 0
    assert text(page, "#link-status") == "" and not page.is_visible("#output") and page.is_visible("#output-hint")
    assert page.evaluate("window.scrollY") == 0
    assert page.evaluate("localStorage.getItem('matches-merge-link')") in ("", None)
    page.click("#mode-review")
    assert page.is_checked("#list .verdict .bad input") and text(page, "#marked") == "1"
    assert problems == []


def test_only_the_last_link_is_remembered_the_picks_are_not(open_page):
    page, problems = open_page(mode="merge")
    ready(page)
    page.reload()
    page.wait_for_selector("#list > *")
    assert page.input_value("#untappd-link") == BEER_LINK        # the link is back, and understood
    assert text(page, "#link-status") == "Пиво № 1941326 · открыть на Untappd"
    assert page.evaluate("localStorage.getItem('matches-merge-link')") == BEER_LINK
    assert text(page, "#picked-count") == "0" and page.input_value("#pick-search") == ""
    assert page.evaluate("Object.keys(localStorage).sort()") == ["matches-merge-link"]   # nothing else about merging is kept
    page.click("#mode-merge")
    page.fill("#untappd-link", "")                               # a link taken out of the field is not brought back
    page.reload()
    page.wait_for_selector("#list > *")
    assert page.input_value("#untappd-link") == ""
    assert problems == []


def test_a_browser_that_blocks_local_storage_still_gets_a_working_merge(open_page):
    page, problems = open_page(mode="merge", init_script=NO_STORAGE)
    ready(page)
    assert page.is_visible("#output") and page.text_content("#yaml-out").endswith("untappd_id: 1941326")
    page.click("#reset-merge")
    assert page.input_value("#untappd-link") == "" and not page.is_visible("#output")
    assert problems == []
