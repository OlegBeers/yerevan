"""The «Найти на Untappd» mode of site/matches.html, run for real: Chrome driven by Playwright, the page and its data.json
served from memory (no network). Skipped where neither the installed Chrome nor Playwright's Chromium can be launched."""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from taps.corrections import parse_corrections
from tests.test_matches_merge_browser import NO_STORAGE, PIXEL, luminance
from tests.test_site_i18n_browser import browser  # noqa: F401  (the shared fixture: a launched Chrome; the import skips this module without Playwright)

PAGE = Path(__file__).resolve().parent.parent / "site" / "matches.html"
ORIGIN = "https://taps.test"
PLACE_IDS = {"dors", "gargoyle", "beer-city", "parma"}
BEER_LINK = "https://untappd.com/b/wolf-s-brewery-indian-pale-ale-ipa/1941326"


def place(id, name):
    return {"id": id, "name": name}


def row(place_id, key, name, brewery=None, section="shops", **kw):
    """A row of data.json as taps/site_data.py writes it, trimmed to the fields the to-do list reads."""
    return {"place_id": place_id, "beer_key": key, "name": name, "brewery": brewery, "abv": None,
            "beer_logo": None, "shop_url": None, "url": None, "section": section, "match_via": None, **kw}


PLACES = [place("dors", "Dors Craft Beer & Kitchen"), place("gargoyle", "Gargoyle Bar"),
          place("beer-city", "Beer City"), place("parma", "Parma")]
DORS_STOUT = row("dors", "n:dors stout", "Dors Stout", "Dargett", section="bars", url="https://buy.am/dors")
BEER_CITY_LAGER = row("beer-city", "n:city lager", "City Lager", "Some Brewery", abv=4.5,
                      shop_url="https://beer-city.am/p/lager", beer_logo="https://img.test/label.png")
PARMA_ALE = row("parma", "n:parma ale", "Parma Ale", "Parma Brewery", abv=5.5, shop_url="https://parma.am/p/1")
LINKED = row("beer-city", "n:linked beer", "Linked Beer", "Brewery X", match_via="local")
BLOCKED = row("beer-city", "n:blocked beer", "Blocked Beer", "Brewery Y", untappd_blocked=True)
BAR_BEER = row("gargoyle", "u:123", "Bar IPA", "Brewery Z", section="bars")
SUGGESTED = row("beer-city", "n:suggested beer", "Suggested Beer", "Some Brewery",
                suggest={"id": 999, "name": "Maybe Beer", "brewery": "Maybe Brewery",
                        "url": "https://untappd.com/beer/999"})


def make_data(*extra_rows):
    return {"places": PLACES, "matches": [],
            "rows": [DORS_STOUT, BEER_CITY_LAGER, PARMA_ALE, LINKED, BLOCKED, BAR_BEER, *extra_rows]}


@pytest.fixture
def open_page(browser):
    """open_page(...) -> (page, problems): matches.html loaded with its data, and the list that collects script errors."""
    contexts = []

    def open_page(data=None, *, width=375, mode="todo", dark=False, status=200, init_script=None, start_hash=""):
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
        # a hash present on this first navigation is read by load()'s importFromHash() -- unlike goto()
        # to an already-open page's URL, which only changes the fragment and never reloads the document
        page.goto(f"{ORIGIN}/matches.html{start_hash}")
        page.wait_for_selector("#list > *")
        if mode != "review":
            page.click(f"#mode-{mode}")
        return page, problems

    yield open_page
    for context in contexts:
        context.close()


def names(page):
    return page.eval_on_selector_all("#todo-list .name", "els => els.map((e) => e.textContent)")


def text(page, selector):
    return " ".join((page.text_content(selector) or "").split())


def size(page, selector):
    box = page.locator(selector).first.bounding_box()
    return box["width"], box["height"]


def card_input(page):
    return page.locator("#todo-list .todo-card input[type=url]").first


NONE_BTN, NOTBEER_BTN = "#todo-list .todo-actions button:nth-child(1)", "#todo-list .todo-actions button:nth-child(2)"


def test_the_third_mode_shows_only_unlinked_shop_beers_bars_and_menus_included(open_page):
    page, problems = open_page()
    assert page.is_visible("#todo") and not page.is_visible("#review") and not page.is_visible("#merge")
    assert [page.get_attribute(f"#mode-{m}", "aria-pressed") for m in ("review", "merge", "todo")] == \
        ["false", "false", "true"]
    # linked, blocked ("не то же") and the bar's own u:-keyed beer are all excluded
    assert set(names(page)) == {"Dors Stout", "City Lager", "Parma Ale"}
    page.click("#mode-review")
    assert page.is_visible("#review") and page.is_visible("#bar") and not page.is_visible("#todo")
    assert problems == []


def test_order_is_bars_and_menus_first_then_shops_both_in_places_order_and_by_name(open_page):
    data = {"places": PLACES, "matches": [], "rows": [
        row("parma", "n:b", "B Beer", "Br"), row("gargoyle", "n:z", "Z Beer", "Br", section="bars"),
        row("dors", "n:m", "M Beer", "Br", section="bars"), row("beer-city", "n:a", "A Beer", "Br"),
        row("dors", "n:a2", "A2 Beer", "Br", section="bars"),
    ]}
    page, problems = open_page(data)
    assert names(page) == ["A2 Beer", "M Beer", "Z Beer", "A Beer", "B Beer"]
    assert problems == []


def test_a_card_shows_photo_name_brewery_place_abv_a_shop_link_and_a_search_link(open_page):
    page, problems = open_page()
    page.fill("#todo-search", "city lager")
    card = page.locator("#todo-list .todo-card")
    assert card.locator(".tag").text_content() == "Beer City"
    assert card.locator(".name").text_content() == "City Lager"
    assert card.locator(".small").all_text_contents() == ["Some Brewery", "4.5%"]
    assert card.locator("img.thumb").get_attribute("src") == BEER_CITY_LAGER["beer_logo"]
    assert card.locator("a.name").get_attribute("href") == BEER_CITY_LAGER["shop_url"]
    search_link = card.get_by_text("Искать на Untappd")
    href = search_link.get_attribute("href")
    assert href.startswith("https://untappd.com/search?q=")
    assert parse_qs(urlsplit(href).query)["q"][0] == "Some Brewery City Lager"
    assert search_link.get_attribute("target") == "_blank"
    assert search_link.get_attribute("rel") == "noopener noreferrer"
    assert problems == []


def test_a_beer_with_no_shop_url_falls_back_to_the_general_url_for_its_link(open_page):
    page, problems = open_page()
    page.fill("#todo-search", "dors stout")
    assert page.locator("#todo-list a.name").get_attribute("href") == DORS_STOUT["url"]
    assert problems == []


def test_the_link_field_validates_inline_like_the_merge_mode(open_page):
    page, problems = open_page()
    page.fill("#todo-search", "parma ale")
    field = card_input(page)
    assert [field.get_attribute(a) for a in ("type", "inputmode", "autocomplete")] == ["url", "url", "off"]
    field.fill("https://untappd.com/brewery/1234")
    assert text(page, "#todo-list .link-status").startswith("Не нашёл номер пива.")
    assert field.get_attribute("aria-invalid") == "true"
    field.fill("https://untappd.com/b/wolf-ipa/1941326?ref=x")
    assert text(page, "#todo-list .link-status") == "Пиво № 1941326"
    assert field.get_attribute("aria-invalid") == "false"
    field.fill("")
    assert text(page, "#todo-list .link-status") == "" and field.get_attribute("aria-invalid") == "false"
    assert problems == []


def test_the_two_buttons_are_mutually_exclusive_and_clear_a_typed_link(open_page):
    page, problems = open_page()
    page.fill("#todo-search", "parma ale")
    field = card_input(page)
    field.fill(BEER_LINK)
    page.click(NONE_BTN)   # «Нет на Untappd» wins over whatever was typed
    assert field.input_value() == "" and page.get_attribute(NONE_BTN, "aria-pressed") == "true"
    assert page.get_attribute(NOTBEER_BTN, "aria-pressed") == "false"
    assert page.locator("#todo-list .card.confirmed").count() == 1
    page.click(NOTBEER_BTN)   # the other button takes over
    assert page.get_attribute(NONE_BTN, "aria-pressed") == "false"
    assert page.get_attribute(NOTBEER_BTN, "aria-pressed") == "true"
    field.fill(BEER_LINK)   # typing a link clears both buttons
    assert page.get_attribute(NOTBEER_BTN, "aria-pressed") == "false"
    assert page.locator("#todo-list .card.confirmed").count() == 1
    page.click(NOTBEER_BTN)
    page.click(NOTBEER_BTN)   # a second tap takes the choice back
    assert page.get_attribute(NOTBEER_BTN, "aria-pressed") == "false"
    assert page.locator("#todo-list .card.confirmed").count() == 0
    assert problems == []


def test_the_counter_and_bottom_bar_track_progress(open_page):
    page, problems = open_page()
    assert text(page, "#todo-count") == "Осталось: 3 · заполнено: 0"
    assert text(page, "#todo-bar").startswith("Готово: 0")
    assert page.is_disabled("#todo-copy-yaml") and page.is_disabled("#todo-copy-text")
    page.fill("#todo-search", "city lager")
    card_input(page).fill(BEER_LINK)
    assert text(page, "#todo-count") == "Осталось: 2 · заполнено: 1"
    assert text(page, "#todo-done") == "1"
    assert not page.is_disabled("#todo-copy-yaml") and not page.is_disabled("#todo-copy-text")
    page.fill("#todo-search", "parma ale")
    page.click(NONE_BTN)
    assert text(page, "#todo-count") == "Осталось: 1 · заполнено: 2"
    page.click(NONE_BTN)   # taking it back
    assert text(page, "#todo-count") == "Осталось: 2 · заполнено: 1"
    assert problems == []


def test_search_and_place_chips_filter_the_list(open_page):
    page, problems = open_page()
    assert set(names(page)) == {"Dors Stout", "City Lager", "Parma Ale"}
    page.fill("#todo-search", "lager")
    assert names(page) == ["City Lager"]
    page.fill("#todo-search", "")
    assert page.locator("#todo-places .place-filter").count() == 3   # dors, beer-city, parma -- gargoyle has none
    chip = "#todo-places button:has-text('Beer City')"
    page.click(chip)
    assert names(page) == ["City Lager"]
    assert page.get_attribute(chip, "aria-pressed") == "true"
    page.click(chip)   # a second tap clears the filter
    assert set(names(page)) == {"Dors Stout", "City Lager", "Parma Ale"}
    assert problems == []


def test_a_suggestion_offers_a_one_tap_fill_and_a_filter_chip(open_page):
    """v1.4 owner suggestion: a doubtful search match (site_data.py's row["suggest"]) is shown as
    "Возможно: ..." with a one-tap button that fills the card's own link field."""
    data = make_data(SUGGESTED)
    page, problems = open_page(data)
    page.fill("#todo-search", "suggested beer")
    card = page.locator("#todo-list .todo-card")
    link = card.get_by_text("Maybe Beer — Maybe Brewery")
    assert link.get_attribute("href") == "https://untappd.com/beer/999"
    assert link.get_attribute("target") == "_blank"
    assert "noopener" in link.get_attribute("rel")
    card.get_by_text("Да, это оно").click()
    assert text(page, "#todo-list .link-status") == "Пиво № 999"
    assert card_input(page).input_value() == "https://untappd.com/beer/999"
    assert problems == []


def test_the_suggestion_filter_chip_only_appears_and_filters_when_something_has_one(open_page):
    page, problems = open_page()   # default fixture has no suggestions
    chip = "#todo-places button:has-text('с подсказкой')"
    assert page.locator(chip).count() == 0
    assert problems == []

    data = make_data(SUGGESTED)
    page2, problems2 = open_page(data)
    assert page2.locator(chip).count() == 1
    page2.click(chip)
    assert names(page2) == ["Suggested Beer"]
    assert page2.get_attribute(chip, "aria-pressed") == "true"
    page2.click(chip)   # a second tap clears the filter
    assert set(names(page2)) == {"Dors Stout", "City Lager", "Parma Ale", "Suggested Beer"}
    assert problems2 == []


def test_progress_survives_a_reload_via_local_storage(open_page):
    page, problems = open_page()
    page.fill("#todo-search", "city lager")
    card_input(page).fill(BEER_LINK)
    page.fill("#todo-search", "parma ale")
    page.click(NOTBEER_BTN)
    stored = page.evaluate("JSON.parse(localStorage.getItem('matches-todo'))")
    assert stored[f"beer-city|{BEER_CITY_LAGER['beer_key']}"] == {"type": "link", "id": "1941326"}
    assert stored[f"parma|{PARMA_ALE['beer_key']}"] == {"type": "notbeer"}
    page.reload()
    page.wait_for_selector("#list > *")
    page.click("#mode-todo")
    assert text(page, "#todo-count") == "Осталось: 1 · заполнено: 2"
    page.fill("#todo-search", "city lager")
    assert card_input(page).input_value() == "https://untappd.com/beer/1941326"
    page.fill("#todo-search", "parma ale")
    assert page.get_attribute(NOTBEER_BTN, "aria-pressed") == "true"
    assert problems == []


def test_a_browser_that_blocks_local_storage_still_gets_a_working_to_do_list(open_page):
    page, problems = open_page(init_script=NO_STORAGE)
    page.fill("#todo-search", "city lager")
    card_input(page).fill(BEER_LINK)
    assert text(page, "#todo-done") == "1"
    assert problems == []


def test_progress_travels_through_the_existing_transfer_link(open_page):
    page, problems = open_page()
    page.fill("#todo-search", "city lager")
    card_input(page).fill(BEER_LINK)
    page.fill("#todo-search", "parma ale")
    page.click(NONE_BTN)
    link = page.evaluate("transferLink()")
    assert "#marks=" in link

    # a fresh context (nothing shared but the link itself), the hash present on the very first navigation:
    # goto() on an already-open page only changes the fragment and never reloads the document, unlike a
    # real second device opening the link for the first time
    page2, problems2 = open_page(start_hash="#" + urlsplit(link).fragment)
    assert text(page2, "#todo-count") == "Осталось: 1 · заполнено: 2"
    page2.fill("#todo-search", "city lager")
    assert card_input(page2).input_value() == "https://untappd.com/beer/1941326"
    page2.fill("#todo-search", "parma ale")
    assert page2.get_attribute(NONE_BTN, "aria-pressed") == "true"
    assert problems == [] and problems2 == []


def test_the_same_as_yaml_round_trips_through_parse_corrections_with_cyrillic_and_quoted_keys(open_page):
    linked = row("beer-city", "n:l'chaim: \"ipa\" \\ x # y", "L'Chaim: \"IPA\" \\ x # y", "Q")
    blocked = row("parma", "n:" + "очень" * 20, "Очень" * 20, "Пивоварня")
    data = make_data(linked, blocked)
    page, problems = open_page(data)
    page.fill("#todo-search", "l'chaim")
    card_input(page).fill(BEER_LINK)
    page.fill("#todo-search", "очень")
    page.click(NONE_BTN)
    yaml_text = page.evaluate("todoYaml()")
    assert yaml_text.startswith("# same_as:\n") and "# hide:" not in yaml_text
    raw = yaml.safe_load(yaml_text.replace("# same_as:", "same_as:", 1))
    corrections, errors = parse_corrections(raw, PLACE_IDS)
    assert errors == []
    assert corrections.same_as == {("beer-city", linked["beer_key"]): 1941326, ("parma", blocked["beer_key"]): None}
    assert problems == []


def test_the_hide_yaml_round_trips_through_parse_corrections_with_an_awkward_key(open_page):
    not_beer = row("gargoyle", "n:brewpub \"special\" # тест", "Brewpub \"Special\" # тест", "Dargett", section="bars")
    data = make_data(not_beer)
    page, problems = open_page(data)
    page.fill("#todo-search", "special")
    page.click(NOTBEER_BTN)
    yaml_text = page.evaluate("todoYaml()")
    assert yaml_text.startswith("# hide:\n") and "# same_as:" not in yaml_text
    raw = yaml.safe_load(yaml_text.replace("# hide:", "hide:", 1))
    corrections, errors = parse_corrections(raw, PLACE_IDS)
    assert errors == []
    assert corrections.hide == {("gargoyle", not_beer["beer_key"])}
    assert problems == []


def test_the_claude_text_has_one_line_per_filled_item_with_the_agreed_outcomes(open_page):
    page, problems = open_page()
    page.fill("#todo-search", "city lager")
    card_input(page).fill(BEER_LINK)
    page.fill("#todo-search", "parma ale")
    page.click(NONE_BTN)
    page.fill("#todo-search", "dors stout")
    page.click(NOTBEER_BTN)
    page.fill("#todo-search", "")
    lines = page.evaluate("todoText()").split("\n")
    assert f"beer-city | {BEER_CITY_LAGER['beer_key']} | City Lager → https://untappd.com/beer/1941326" in lines
    assert f"parma | {PARMA_ALE['beer_key']} | Parma Ale → нет на Untappd" in lines
    assert f"dors | {DORS_STOUT['beer_key']} | Dors Stout → не пиво" in lines
    assert len(lines) == 3
    assert problems == []


def test_the_copy_buttons_put_the_agreed_text_on_the_clipboard(open_page):
    page, problems = open_page()
    page.fill("#todo-search", "city lager")
    card_input(page).fill(BEER_LINK)
    page.click("#todo-copy-yaml")
    page.wait_for_function("document.getElementById('todo-copy-status').textContent !== ''")
    assert text(page, "#todo-copy-status") == "Скопировано"
    assert page.evaluate("navigator.clipboard.readText()") == page.evaluate("todoYaml()")
    page.click("#todo-copy-text")
    page.wait_for_function("document.getElementById('todo-copy-status').textContent === 'Скопировано'")
    assert page.evaluate("navigator.clipboard.readText()") == page.evaluate("todoText()")
    assert problems == []


def test_a_failed_load_is_said_in_the_to_do_panel_too(open_page):
    page, _ = open_page(status=500)
    assert text(page, "#todo-list") == "Не удалось загрузить данные. Обновите страницу через минуту."


def test_everything_to_tap_is_at_least_44px_high_on_a_phone(open_page):
    page, problems = open_page()
    page.fill("#todo-search", "parma ale")
    # the beer's own name is a small inline link, like the review mode's shop/Untappd name links elsewhere
    # on this page -- 44px applies to the actual tap targets: the field, the two buttons, the search link
    for selector in ("#mode-todo", "#todo-search", "#todo-list .todo-card input[type=url]", NONE_BTN, NOTBEER_BTN,
                     "text=Искать на Untappd"):
        assert size(page, selector)[1] >= 44, selector
    assert problems == []


def luminance_of(page, selector):
    return luminance(page.eval_on_selector(selector, "(e) => getComputedStyle(e).backgroundColor"))


def test_the_dark_theme_reaches_the_to_do_panel(open_page):
    page, problems = open_page(dark=True)
    field = "#todo-list .todo-card input[type=url]"
    for selector in ("body", "#todo-bar", "#todo-search", ".todo-card", field, ".place-filter"):
        assert luminance_of(page, selector) < 0.25, selector
    for selector in ("#todo-list .name", field, "#todo-count"):
        color = page.eval_on_selector(selector, "(e) => getComputedStyle(e).color")
        assert luminance(color) > 0.5, (selector, color)
    assert problems == []


@pytest.mark.parametrize("width", [320, 375, 1280])
def test_nothing_is_cut_off_or_pushed_out_of_the_screen_at_any_width(open_page, width):
    long_name = "Оченьдлинноеназваниебезпробеловикакихлибоподсказок" * 3
    data = make_data(row("parma", f"n:{long_name.lower()}", long_name, long_name))
    page, problems = open_page(data, width=width)
    page.fill("#todo-search", "оченьдлинное")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for selector in ("#todo-list", "#todo-list *", "#mode-review", "#mode-todo", "#todo-bar", "#todo-search"):
        assert page.eval_on_selector_all(selector, "(els) => els.every((e) => e.getBoundingClientRect().left >= 0 && "
                                                   "e.getBoundingClientRect().right <= window.innerWidth + 0.5)"), selector
    assert problems == []
