"""The RU | EN interface of site/index.html, run for real: Chrome driven by Playwright, the page and its data.json served
from memory (no network). Skipped where neither the installed Chrome nor Playwright's Chromium can be launched."""
import base64
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PAGE = Path(__file__).resolve().parent.parent / "site" / "index.html"
ORIGIN = "https://taps.test"
CYRILLIC = re.compile(r"[\u0400-\u04FF]")
TABS = ("tab-all", "tab-bars", "tab-shops", "tab-venues")
SHOP_IDS = ("beercity", "parma")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def make_data(*, generated_ago=timedelta(hours=5), rows=None):
    """A data.json with every kind of row, place and venue the page draws. All names are Latin, so any Cyrillic that shows
    up on the English page is interface text nobody translated."""
    now = datetime.now(timezone.utc)

    def ago(**delta):
        return (now - timedelta(**delta)).isoformat(timespec="seconds")

    def place(id, name, section, **kw):
        return {"id": id, "name": name, "kind": "shop" if section == "shops" else "bar", "section": section,
                "last_ok": None, "menu_updated_at": None, "failing": False, "failing_days": 0, "logo": None,
                "verified": False, "untappd_url": None, "address": None, "map_url": None, **kw}

    def row(place_id, name, badge, hours=10, **kw):
        return {"place_id": place_id, "section": "shops" if place_id in SHOP_IDS else "bars", "beer_key": f"n:{name.lower()}",
                "name": name, "brewery": "Test Brewery", "style": None, "abv": None, "ibu": None, "rating": None,
                "price_amd": None, "volume_ml": None, "container": None, "url": None, "serving": None, "shop_url": None,
                "country": None, "servings": None, "style_inferred": False, "badge": badge, "since": now.date().isoformat(),
                "since_at": ago(hours=hours), "seen_days_ago": None, "new": False, "star": False, "by": None,
                "beer_logo": None, "shop_name": None, "match_via": None, **kw}

    map_url = "https://yandex.com/maps/?text=Test"
    places = [
        place("gargoyle", "Gargoyle Bar", "bars", verified=True, address="1 Test St", map_url=map_url,
              untappd_url="https://untappd.com/v/gargoyle-bar/1", last_ok=ago(seconds=5), menu_updated_at=ago(days=2)),
        place("beatles", "Beatles Pub", "bars", failing=True, failing_days=3, last_ok=ago(days=3), map_url=map_url,
              menu_updated_at=ago(days=1)),
        place("ferment", "Ferment Bar", "bars", failing=True, menu_updated_at=ago(seconds=5)),
        place("newbar", "New Bar", "bars"),
        place("beercity", "Beer City", "shops", address="2 Test Ave", last_ok=ago(days=1)),
        place("parma", "Parma", "shops", last_ok=ago(days=5)),
    ]
    rows = rows if rows is not None else [
        row("gargoyle", "Test IPA", "menu", hours=3, style="IPA", abv=6.5, ibu=60, rating=4.25, price_amd=2800,
            volume_ml=400, container="draft", url="https://untappd.com/b/test-ipa/1", new=True, star=True, country="Armenia"),
        row("beatles", "Dark Stout", "checkin", hours=30, style="Stout", abv=8.0, rating=3.9, serving="Draft", seen_days_ago=0),
        row("beatles", "Tiny Ale", "checkin", hours=31, serving="Taster", seen_days_ago=1),
        row("beatles", "Cask Ale", "checkin", hours=32, serving="Cask", seen_days_ago=5),
        row("beatles", "Mystery Ale", "checkin", hours=33),
        row("ferment", "Friend Sour", "manual", hours=40, by="Ivan"),
        row("ferment", "Anon Gose", "manual", hours=41),
        row("beercity", "Shop Pils", "shop", hours=50, style="Pilsner", style_inferred=True, container="can", volume_ml=500,
            price_amd=900, country="Czech Republic", shop_url="https://shop.test/pils"),
        row("parma", "Odd Lager", "shop", hours=51, container="tin", price_amd=700, country="Freedonia"),
        # one beer at a bar (two servings) and at a shop (called something else there): one card, two place lines
        row("gargoyle", "Multi Beer", "menu", hours=60, group_key="u:99", beer_key="u:99", abv=5.0, price_amd=2800,
            container="draft", servings=[{"container": "draft", "price_amd": 2800},
                                         {"container": "bottle", "price_amd": 1500, "volume_ml": 330}]),
        row("beercity", "Multi Beer", "shop", hours=61, group_key="u:99", shop_name="Multi Beer 0.33l", match_via="search",
            container="bottle", price_amd=1500, volume_ml=330, shop_url="https://shop.test/multi"),
    ]
    venues = [{"name": f"Venue {n}", "url": None, "logo": None, "verified": False, "checkins_30d": n,
               "last_checkin": ago(days=n) if n != 22 else None, "tracked": n % 2 == 1,
               "address": "3 Test Rd" if n < 5 else None, "map_url": map_url} for n in (1, 2, 5, 11, 21, 22)]
    return {"generated_at": (now - generated_ago).isoformat(timespec="seconds"), "started_at": "2026-08-01T00:00:00+00:00",
            "hot_rating": 4.1, "places": places, "rows": rows, "venues": venues, "matches": []}


# every string the page shows, wherever it hides: text, tooltips, labels, placeholders, the title and the description
STRINGS_JS = """() => {
  const out = [document.title, document.querySelector('meta[name=description]').content];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode())
    if (!['SCRIPT', 'STYLE'].includes(n.parentElement.tagName) && n.textContent.trim()) out.push(n.textContent.trim());
  for (const el of document.querySelectorAll('[aria-label],[title],[placeholder]')) {
    if (el.closest('.lang button')) continue;   // "Русский" / "English": each language is named in its own
    for (const a of ['aria-label', 'title', 'placeholder']) if (el.hasAttribute(a)) out.push(el.getAttribute(a));
  }
  return out;
}"""


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        for options in ({"channel": "chrome"}, {}):   # the installed Chrome first, then Playwright's own Chromium
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
    """open_page(...) -> (page, problems): the page loaded with its data, and the list that collects script errors."""
    contexts = []

    def open_page(data=None, *, locale="en-US", query="", width=1280, status=200, storage_blocked=False):
        context = browser.new_context(locale=locale, viewport={"width": width, "height": 900})
        context.set_default_timeout(5000)   # everything is served from memory: a broken page should fail fast
        contexts.append(context)
        if storage_blocked:
            context.add_init_script("Object.defineProperty(window, 'localStorage', "
                                    "{ get() { throw new DOMException('blocked', 'SecurityError'); } });")
        page = context.new_page()
        problems = []
        page.on("pageerror", lambda error: problems.append(str(error)))
        page.on("console", lambda msg: problems.append(msg.text) if msg.type == "error" and status == 200 else None)

        def serve(route):
            url = urlsplit(route.request.url)
            if url.path.endswith("/index.html"):
                route.fulfill(body=PAGE.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
            elif url.path.endswith("/data.json"):
                route.fulfill(status=status, json=data if data is not None else make_data())
            elif url.netloc.startswith("fonts."):
                route.fulfill(body="", content_type="text/css")
            elif url.netloc == "logo.test" and url.path == "/ok.png":
                route.fulfill(body=PNG, content_type="image/png")
            else:
                route.abort()

        page.route("**/*", serve)
        page.goto(f"{ORIGIN}/index.html{query}")
        page.wait_for_selector("#rows > *")
        return page, problems

    yield open_page
    for context in contexts:
        context.close()


def strings(page):
    return page.evaluate(STRINGS_JS)


def text(page, selector):
    return " ".join((page.text_content(selector) or "").split())


def titles(page, selector):
    return page.eval_on_selector_all(selector, "els => els.map((e) => e.title)")


def venue_lines(page):
    return page.eval_on_selector_all("#venues li", "lis => lis.map((li) => [...li.querySelectorAll('.meta')].map((m) => m.textContent))")


def lang_state(page):
    return (page.get_attribute("html", "lang"),
            [page.get_attribute(f".lang [data-lang={lang}]", "aria-pressed") for lang in ("ru", "en")])


@pytest.mark.parametrize("locale, expected", [
    ("ru-RU", "ru"), ("ru", "ru"), ("en-US", "en"), ("en-GB", "en"), ("uk-UA", "en"), ("hy-AM", "en"), ("de-DE", "en"),
])
def test_the_first_visit_follows_the_browsers_language(open_page, locale, expected):
    page, problems = open_page(locale=locale)
    assert lang_state(page) == (expected, ["true", "false"] if expected == "ru" else ["false", "true"])
    assert text(page, "#tab-all") == ("🔎Всё пиво" if expected == "ru" else "🔎All beer")
    assert page.title() == ("Ереван на кранах" if expected == "ru" else "Yerevan on Tap")
    assert problems == []


def test_a_language_in_the_url_wins_over_the_browser_and_is_remembered(open_page):
    page, problems = open_page(locale="ru-RU", query="?lang=en")
    assert lang_state(page) == ("en", ["false", "true"])
    assert page.evaluate("localStorage.getItem('yerevan.lang')") == "en"
    page.goto(f"{ORIGIN}/index.html")            # next visit, no ?lang=: the remembered choice beats the browser's
    page.wait_for_selector("#rows > *")
    assert lang_state(page) == ("en", ["false", "true"])
    page.goto(f"{ORIGIN}/index.html?lang=RU")    # the URL beats the remembered choice; case does not matter
    page.wait_for_selector("#rows > *")
    assert lang_state(page) == ("ru", ["true", "false"])
    assert page.evaluate("localStorage.getItem('yerevan.lang')") == "ru"
    assert problems == []


@pytest.mark.parametrize("query", ["?lang=fr", "?lang=", "?lang=toString", "?lang=__proto__", "?lang=constructor"])
def test_an_unknown_language_in_the_url_is_ignored(open_page, query):
    page, problems = open_page(locale="ru-RU", query=query)
    assert lang_state(page) == ("ru", ["true", "false"])
    assert page.evaluate("localStorage.getItem('yerevan.lang')") is None
    assert problems == []


def test_switching_repaints_in_place_and_keeps_the_filters_the_sort_and_the_open_tab(open_page):
    page, problems = open_page()
    page.evaluate("window.notReloaded = true")
    page.click("#tab-bars")
    page.fill("#search", "ipa")
    page.select_option("#sort", "rating")
    page.check("#only-new")
    page.click("#places .place:nth-child(2)")      # Gargoyle Bar

    def state():
        return (page.input_value("#search"), page.input_value("#sort"), page.is_checked("#only-new"),
                page.get_attribute("[data-section][aria-pressed=true]", "data-section"),
                text(page, "#places .place[aria-pressed=true]"), text(page, "#rows .beer"))

    before = state()
    assert before[:4] == ("ipa", "rating", True, "bars") and "Gargoyle Bar" in before[4]
    assert text(page, "#count") == "Showing 1 of 8"
    page.click(".lang [data-lang=ru]")
    assert state() == before and page.evaluate("window.notReloaded") is True
    assert text(page, "#count") == "Показано 1 из 8"
    assert [text(page, f"#sort option:nth-child({i})") for i in (1, 2, 3)] == ["по новизне", "по рейтингу", "по крепости"]
    page.click(".lang [data-lang=en]")
    assert state() == before and text(page, "#count") == "Showing 1 of 8"
    assert [text(page, f"#sort option:nth-child({i})") for i in (1, 2, 3)] == ["newest", "rating", "ABV"]
    assert problems == []


def test_the_switch_updates_html_lang_the_buttons_the_static_texts_and_what_it_remembers(open_page):
    page, problems = open_page(locale="ru-RU")
    page.click(".lang [data-lang=en]")
    assert lang_state(page) == ("en", ["false", "true"])
    assert page.evaluate("localStorage.getItem('yerevan.lang')") == "en"
    assert page.title() == "Yerevan on Tap"
    assert page.get_attribute("meta[name=description]", "content").startswith("Craft beer in Yerevan")
    assert page.get_attribute("#search", "placeholder") == "Name, style or brewery"
    assert page.get_attribute("#to-top", "aria-label") == "Back to top"
    page.click(".lang [data-lang=ru]")
    assert lang_state(page) == ("ru", ["true", "false"])
    assert page.evaluate("localStorage.getItem('yerevan.lang')") == "ru"
    assert page.get_attribute("#search", "placeholder") == "Название, стиль или пивоварня"
    assert problems == []


@pytest.mark.parametrize("width", [1280, 375])   # a table and cards
def test_the_english_page_has_no_russian_left_on_any_tab(open_page, width):
    page, problems = open_page(width=width)
    for tab in TABS:
        page.click(f"#{tab}")
        page.click(".lang [data-lang=ru]")           # the switch repaints whatever tab is open
        page.click(".lang [data-lang=en]")
        left = [s for s in strings(page) if CYRILLIC.search(s)]
        assert left == [], (tab, left)
    page.click("#tab-all")
    page.fill("#search", "no such beer")            # the "nothing found" message
    assert text(page, "#rows") == "Nothing found — try a different search or filter."
    assert [s for s in strings(page) if CYRILLIC.search(s)] == []
    assert problems == []


@pytest.mark.parametrize("width", [1280, 375])
def test_the_russian_page_has_no_english_interface_text_left(open_page, width):
    """The mirror image: a word that slipped past t() would stay in English on the Russian page."""
    page, problems = open_page(locale="ru-RU", width=width)
    words = re.compile(r"\b(?:today|yesterday|ago|updated|checked|menu|seen|draft|bottle|shop|listed|Showing|Beers|Nothing|"
                       r"Where|Appeared|Legend|Sources|Section|Language|Search|Sort|Back)\b")
    for tab in TABS:
        page.click(f"#{tab}")
        left = [s for s in strings(page) if words.search(s)]
        assert left == [], (tab, left)
    assert problems == []


def test_russian_wording_on_screen_is_what_the_page_has_always_said(open_page):
    page, problems = open_page(locale="ru-RU")
    assert text(page, "#count") == "Позиций: 10"
    assert text(page, "#updated") == "обновлено 5 ч назад"
    assert text(page, "#legend-star") == "⭐ — возможно, впервые в Ереване (с тех пор, как следим, с 01.08)"
    assert text(page, "#legend-hot") == "🔥 — рейтинг Untappd от 4.1"
    assert [text(page, f"thead th:nth-child({i})") for i in range(1, 9)] == [
        "Пиво", "Стиль", "Крепость", "IBU", "Рейтинг", "Цена", "Где", "Появилось"]
    rows = text(page, "#rows")
    for wanted in ("✅меню", "чекин", "👀розлив, видели сегодня", "👀дегустационный, видели вчера", "👀из бочки, видели 5 дней назад",
                   "✍️со слов (Ivan)", "🛒в магазине", "в магазине: Multi Beer 0.33l", "Чехия", "Freedonia", "Армения",
                   "розлив · 2800 ֏", "бутылка 330 мл · 1500 ֏", "банка 500 мл · 900 ֏", "400 мл · 2800 ֏"):
        assert wanted in rows, wanted
    assert titles(page, "#rows .chip-flag") == ["возможно, впервые в Ереване", "новинка за 7 дней"]
    assert "Верифицирован в Untappd" in titles(page, "#rows .verified")
    assert titles(page, "#rows .hot") == ["рейтинг Untappd"] and titles(page, "#rows .inferred") == ["определено по названию"]
    assert page.get_attribute("#rows .map-link", "aria-label") == "Открыть на Яндекс Картах: 1 Test St"
    page.click("#tab-bars")
    tips = titles(page, "#places .place")
    assert tips[1].startswith("1 Test St · меню обновлено 2 дня назад · проверено сегодня в ")
    assert tips[2] == "меню обновлено вчера · не удалось проверить 3 дня"
    assert tips[3] == "меню обновлено сегодня · последняя проверка не удалась"
    assert tips[4] == "ещё не проверялось"
    assert text(page, "#places .place:first-child") == "Все"
    page.click("#tab-shops")
    tips = titles(page, "#places .place")
    assert re.fullmatch(r"2 Test Ave · проверено вчера в \d\d:\d\d", tips[1])
    assert re.fullmatch(r"проверено \d\d\.\d\d в \d\d:\d\d", tips[2])
    page.click("#tab-venues")
    lines = venue_lines(page)
    assert [line[0] for line in lines] == ["1 чекин за 60 дней", "2 чекина за 60 дней", "5 чекинов за 60 дней",
                                            "11 чекинов за 60 дней", "21 чекин за 60 дней", "22 чекина за 60 дней"]
    assert all(re.fullmatch(r"последний \d\d\.\d\d", line[1]) for line in lines[:5]) and len(lines[5]) == 1
    assert text(page, "#venues li:nth-child(1) .tracked-badge") == "в списке"
    assert page.get_attribute("#venues li:nth-child(6) .map-pin", "aria-label").startswith("Открыть на Яндекс Картах: ")   # no address: the pin alone
    assert problems == []


def test_english_wording_on_screen(open_page):
    page, problems = open_page()
    assert text(page, "#count") == "Beers: 10"
    assert text(page, "#updated") == "updated 5 h ago"
    assert text(page, "#legend-star") == "⭐ — possibly the first time in Yerevan (since we started tracking on 01.08)"
    assert text(page, "#legend-hot") == "🔥 — Untappd rating of 4.1 or higher"
    assert [text(page, f"thead th:nth-child({i})") for i in range(1, 9)] == [
        "Beer", "Style", "ABV", "IBU", "Rating", "Price", "Where", "Appeared"]
    rows = text(page, "#rows")
    for wanted in ("✅menu", "check-in", "👀draft, seen today", "👀taster, seen yesterday", "👀cask, seen 5 days ago",
                   "✍️reported by Ivan", "🛒in the shop", "shop name: Multi Beer 0.33l", "Czech Republic", "Freedonia", "Armenia",
                   "draft · 2800 ֏", "bottle 330 ml · 1500 ֏", "can 500 ml · 900 ֏", "400 ml · 2800 ֏"):
        assert wanted in rows, wanted
    assert titles(page, "#rows .chip-flag") == ["possibly the first time in Yerevan", "new in the last 7 days"]
    assert "Verified on Untappd" in titles(page, "#rows .verified")
    assert titles(page, "#rows .hot") == ["Untappd rating"] and titles(page, "#rows .inferred") == ["guessed from the name"]
    assert page.get_attribute("#rows .map-link", "aria-label") == "Open in Yandex Maps: 1 Test St"
    page.click("#tab-bars")
    tips = titles(page, "#places .place")
    assert tips[1].startswith("1 Test St · menu updated 2 days ago · checked today at ")
    assert tips[2] == "menu updated yesterday · couldn't check for 3 days"
    assert tips[3] == "menu updated today · last check failed"
    assert tips[4] == "not checked yet"
    assert text(page, "#places .place:first-child") == "All"
    page.click("#tab-venues")
    lines = venue_lines(page)
    assert [line[0] for line in lines] == ["1 check-in in the last 60 days", "2 check-ins in the last 60 days",
                                            "5 check-ins in the last 60 days", "11 check-ins in the last 60 days",
                                            "21 check-ins in the last 60 days", "22 check-ins in the last 60 days"]
    assert all(re.fullmatch(r"last check-in \d\d\.\d\d", line[1]) for line in lines[:5])
    assert text(page, "#venues li:nth-child(1) .tracked-badge") == "listed"
    assert text(page, "#venues li:nth-child(1) .map-link") == "3 Test Rd"
    assert page.get_attribute("#venues li:nth-child(6) .map-pin", "aria-label").startswith("Open in Yandex Maps: ")   # no address: the pin alone
    assert page.get_attribute("#venues li:nth-child(1) .map-link", "aria-label") == "Open in Yandex Maps: 3 Test Rd"
    assert problems == []


def test_an_empty_list_says_so_in_either_language(open_page):
    page, problems = open_page(make_data(rows=[]))
    assert text(page, "#rows") == "Nothing here yet." and text(page, "#count") == "Beers: 0"
    page.click(".lang [data-lang=ru]")
    assert text(page, "#rows") == "Пока пусто." and text(page, "#count") == "Позиций: 0"
    assert problems == []


@pytest.mark.parametrize("lang, days, checkins", [
    ("ru", {0: "дней", 1: "день", 2: "дня", 3: "дня", 4: "дня", 5: "дней", 10: "дней", 11: "дней", 12: "дней", 13: "дней",
            14: "дней", 15: "дней", 20: "дней", 21: "день", 22: "дня", 25: "дней", 100: "дней", 101: "день", 111: "дней",
            112: "дней", 121: "день"},
     ["чекинов", "чекин", "чекина", "чекинов", "чекин"]),
    ("en", {0: "days", 1: "day", 2: "days", 5: "days", 11: "days", 21: "days"},
     ["check-ins", "check-in", "check-ins", "check-ins", "check-ins"]),
])
def test_counted_words_take_the_form_their_language_needs(open_page, lang, days, checkins):
    page, problems = open_page(query=f"?lang={lang}")
    for n, form in days.items():
        assert page.evaluate(f"plural({n}, 'unit.day')") == form, n
    assert page.evaluate("days(3)") == ("3 дня" if lang == "ru" else "3 days")
    assert page.evaluate("[0, 1, 2, 5, 21].map((n) => plural(n, 'unit.checkin'))") == checkins
    assert problems == []


def test_a_browser_that_blocks_local_storage_still_gets_a_working_switch(open_page):
    page, problems = open_page(locale="ru-RU", storage_blocked=True)
    assert lang_state(page) == ("ru", ["true", "false"])
    page.click(".lang [data-lang=en]")
    assert lang_state(page) == ("en", ["false", "true"]) and text(page, "#count") == "Beers: 10"
    page, problems_with_url = open_page(locale="ru-RU", query="?lang=en", storage_blocked=True)
    assert lang_state(page) == ("en", ["false", "true"])
    assert problems == [] and problems_with_url == []


def test_the_load_error_is_translated_and_follows_the_switch(open_page):
    page, _ = open_page(status=500)
    assert text(page, "#updated") == "no data"
    assert text(page, "#rows") == "Could not load the data. Please refresh the page in a minute."
    page.click(".lang [data-lang=ru]")
    assert text(page, "#updated") == "нет данных"
    assert text(page, "#rows") == "Не удалось загрузить данные. Обновите страницу через минуту."


def test_the_stale_banner_and_the_update_line_follow_the_language(open_page):
    page, problems = open_page(make_data(generated_ago=timedelta(hours=60)))
    assert page.is_visible("#stale-banner")
    assert re.fullmatch(r"⚠️ The data is out of date: last updated \d\d\.\d\d at \d\d:\d\d\.", text(page, "#stale-banner"))
    assert text(page, "#updated") == "updated 2 days ago"
    page.click(".lang [data-lang=ru]")
    assert page.is_visible("#stale-banner")
    assert re.fullmatch(r"⚠️ Данные устарели: последнее обновление \d\d\.\d\d в \d\d:\d\d\.", text(page, "#stale-banner"))
    assert text(page, "#updated") == "обновлено 2 дня назад"
    page, _ = open_page(make_data(generated_ago=timedelta(minutes=20)))
    assert not page.is_visible("#stale-banner") and text(page, "#updated") == "updated less than an hour ago"
    assert problems == []


def test_a_shared_language_link_follows_the_switch_and_a_plain_link_stays_plain(open_page):
    page, problems = open_page(query="?lang=en")
    page.click(".lang [data-lang=ru]")
    assert urlsplit(page.url).query == "lang=ru"
    page.reload()
    page.wait_for_selector("#rows > *")
    assert lang_state(page) == ("ru", ["true", "false"])
    page, _ = open_page(locale="ru-RU")
    page.click(".lang [data-lang=en]")
    assert urlsplit(page.url).query == ""
    assert problems == []


def layout_data(*, broken_picture=False):
    """make_data() plus the beers a card has to cope with: a name that wraps to four lines beside a rating, a picture that
    loads (and, on request, one that does not: the browser logs that as an error), and a beer with a flag but no rating."""
    data = make_data()
    base = data["rows"][0]                                     # Test IPA: a hot rating and both flags
    when = lambda hours: (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    data["rows"] += [
        {**base, "name": "Carried Away (Apricot, Pear, Quince, Cardamom) Imperial Pastry Sour Ale", "beer_key": "n:long",
         "rating": 3.55, "new": False, "star": False, "since_at": when(4), "beer_logo": "https://logo.test/ok.png"},
        {**base, "name": "Fresh Unrated", "beer_key": "n:fresh", "rating": None, "star": False, "since_at": when(5)},
    ]
    if broken_picture:
        data["rows"].append({**base, "name": "Broken Picture", "beer_key": "n:broken", "rating": 3.2, "new": False, "star": False,
                             "since_at": when(6), "beer_logo": "https://logo.test/missing.png"})
    return data


CARDS_JS = """() => [...document.querySelectorAll('#rows .card')].map((card) => {
  const rect = (selector, root = card) => {
    const e = root.querySelector(selector);
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height};
  };
  const kinds = (parent) => [...parent.children].map((c) => c.classList[0]);
  return {
    title: card.querySelector('.card-name').textContent.trim(),
    parts: kinds(card), head: kinds(card.querySelector('.card-head')),
    blocks: [...card.querySelectorAll('.place-block')].map(kinds),
    box: rect('.card-head'), name: rect('.card-name'), thumb: rect('.thumb'), slot: rect('.rate-slot'),
    chip: rect('.chip-rating'), hot: !!card.querySelector('.chip-rating.hot'),
    flags: [...card.querySelectorAll('.rate-slot .chip-flag')].map((f) => { const r = f.getBoundingClientRect(); return {left: r.left, right: r.right}; }),
    scroll: [card.scrollWidth, card.clientWidth],
  };
})"""


def cards(page):
    return {card["title"]: card for card in page.evaluate(CARDS_JS)}


BOX_JS = "e => { const r = e.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height}; }"


def box(page, selector):
    return page.eval_on_selector(selector, BOX_JS)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_on_a_phone_the_language_switch_shares_the_title_line_as_a_compact_pill(open_page, lang):
    page, problems = open_page(width=375, query=f"?lang={lang}")
    title, switch, head = box(page, "h1"), box(page, ".lang"), box(page, ".head")
    assert title["right"] <= switch["left"] + 0.5                       # beside the title, not above or under it
    assert abs(switch["top"] - title["top"]) <= 2                       # aligned to the top of the line
    assert switch["bottom"] <= title["bottom"] + 1                      # no row of its own: the line is as tall as the title
    assert 26 <= switch["height"] <= 32                                 # a small pill
    assert abs(switch["right"] - head["right"]) <= 0.5                  # pinned to the right edge
    for selector in (".lang [data-lang=ru]", ".lang [data-lang=en]"):   # the finger still gets 44px
        area = page.eval_on_selector(selector, "e => { const s = getComputedStyle(e, '::after'); "
                                               "return [parseFloat(s.width), parseFloat(s.height)]; }")
        assert min(area) >= 44, (selector, area)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert problems == []


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_a_long_title_wraps_under_itself_and_leaves_the_switch_where_it_is(open_page, lang):
    page, problems = open_page(width=375, query=f"?lang={lang}")
    before = box(page, ".lang")
    page.evaluate("document.querySelector('h1 [data-i18n=title]').textContent += ' Yerevan on Tap Yerevan on Tap'")
    after, title = box(page, ".lang"), box(page, "h1")
    assert title["height"] > 1.5 * before["height"]                     # it did wrap
    assert (after["top"], after["right"], after["height"]) == (before["top"], before["right"], before["height"])
    assert title["right"] <= after["left"] + 0.5
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert problems == []


@pytest.mark.parametrize("width", [375, 1280])
def test_every_control_is_at_least_44px_tall(open_page, width):
    page, problems = open_page(width=width)
    for selector in ("#tab-all", "#tab-bars", "#tab-shops", "#tab-venues", "#search", "#sort", ".toggle", "#places .place"):
        heights = page.eval_on_selector_all(selector, "els => els.map((e) => e.getBoundingClientRect().height)")
        assert heights and min(heights) >= 44, (selector, heights)
    assert problems == []


def test_the_place_chips_fade_out_at_the_right_edge_and_the_last_chip_clears_the_fade(open_page):
    page, problems = open_page(width=375)
    mask = page.eval_on_selector("#places", "e => getComputedStyle(e).maskImage || getComputedStyle(e).webkitMaskImage")
    assert "linear-gradient" in mask
    page.eval_on_selector("#places", "e => { e.scrollLeft = e.scrollWidth; }")
    last, row = box(page, "#places .place:last-child"), box(page, "#places")
    assert row["right"] - last["right"] >= 23.5                           # scrolled to the end, the last chip is past the fade
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert problems == []


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_every_card_has_the_same_anatomy(open_page, lang):
    page, problems = open_page(layout_data(broken_picture=True), width=375, query=f"?lang={lang}")
    page.wait_for_function("!document.querySelector('#rows img.thumb[src*=\"missing\"]')")   # the broken picture was swapped out
    shapes = page.evaluate(CARDS_JS)
    assert len(shapes) == 13
    order = ["chips", "map-link", "shop-name"]
    for shape in shapes:
        assert shape["parts"] in (["card-head", "meta", "place-blocks", "card-foot"], ["card-head", "place-blocks", "card-foot"]), shape["title"]
        assert shape["head"][:2] == ["thumb", "card-name"] and shape["head"][2:] in ([], ["rate-slot"]), shape["title"]
        assert shape["blocks"], shape["title"]
        for block in shape["blocks"]:
            assert block[0] == "pb-head" and block[1:] == [part for part in order if part in block[1:]], (shape["title"], block)
    assert [len(shape["blocks"]) for shape in shapes if shape["title"] == "Multi Beer"] == [2]   # one block per place
    assert all(abs(shape["thumb"]["width"] - 44) < 0.5 and abs(shape["thumb"]["height"] - 44) < 0.5 for shape in shapes)   # a photo, a tile or a broken picture: 44px
    assert len(problems) == 1 and problems[0].startswith("Failed to load resource")                # the broken picture, and nothing else


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_the_rating_chip_is_pinned_to_the_top_right_and_is_the_same_size_on_every_card(open_page, lang):
    page, problems = open_page(layout_data(), width=375, query=f"?lang={lang}")
    rated = [card for card in cards(page).values() if card["chip"]]
    assert len(rated) >= 3 and {card["hot"] for card in rated} == {True, False}   # hot and plain chips both
    assert len({(round(card["chip"]["width"], 1), round(card["chip"]["height"], 1)) for card in rated}) == 1
    for card in rated:
        assert abs(card["chip"]["right"] - card["box"]["right"]) <= 0.5, card["title"]           # the right edge of the card, always
        assert card["chip"]["top"] - card["box"]["top"] <= 12, card["title"]                     # in the top band, never under the name
        assert card["name"]["right"] <= card["slot"]["left"] - 11, card["title"]                  # the name stops before the chip
    hot = page.evaluate("[...document.querySelectorAll('#rows .chip-rating')].map((c) => [c.classList.contains('hot'), c.firstChild.textContent])")
    assert sorted(set(map(tuple, hot))) == [(False, "★"), (True, "🔥")]
    assert problems == []


def test_a_beer_without_a_rating_or_a_flag_gives_its_name_the_full_width(open_page):
    page, problems = open_page(layout_data(), width=375)
    everything = cards(page)
    bare = [card for card in everything.values() if not card["slot"]]
    assert len(bare) >= 5
    for card in bare:
        assert abs(card["name"]["right"] - card["box"]["right"]) <= 0.5, card["title"]           # no chip, no gap
    assert all(card["name"]["right"] < card["box"]["right"] - 40 for card in everything.values() if card["chip"])
    assert problems == []


def test_a_name_starts_at_the_same_place_on_every_card_and_a_long_one_wraps_in_its_column(open_page):
    page, problems = open_page(layout_data(), width=375)
    everything = cards(page)
    assert {round(card["name"]["left"] - card["box"]["left"], 1) for card in everything.values()} == {56.0}   # photo 44 + gap 12
    long = everything["Carried Away (Apricot, Pear, Quince, Cardamom) Imperial Pastry Sour Ale"]
    short = everything["Dark Stout"]
    assert long["name"]["height"] > 3 * 20                                                       # four lines or so
    assert abs((long["chip"]["top"] - long["box"]["top"]) - (short["chip"]["top"] - short["box"]["top"])) <= 1   # the chip did not move
    assert long["scroll"][0] <= long["scroll"][1]
    assert problems == []


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_the_new_and_first_time_flags_sit_left_of_the_rating_chip_and_do_not_shift_the_name(open_page, lang):
    page, problems = open_page(layout_data(), width=375, query=f"?lang={lang}")
    everything = cards(page)
    ipa = everything["Test IPA"]
    assert len(ipa["flags"]) == 2
    assert all(flag["right"] <= ipa["chip"]["left"] + 0.5 for flag in ipa["flags"])
    assert ipa["flags"][0]["right"] <= ipa["flags"][1]["left"] + 0.5
    assert abs(ipa["name"]["left"] - everything["Dark Stout"]["name"]["left"]) < 0.5           # the name starts where it always does
    fresh = everything["Fresh Unrated"]                                                          # a flag, no rating
    assert fresh["slot"] and not fresh["chip"] and len(fresh["flags"]) == 1
    assert abs(fresh["slot"]["right"] - fresh["box"]["right"]) <= 0.5
    titles_ = page.eval_on_selector_all("#rows .card:first-child .chip-flag", "els => els.map((e) => e.title)")
    assert titles_ == (["возможно, впервые в Ереване", "новинка за 7 дней"] if lang == "ru"
                       else ["possibly the first time in Yerevan", "new in the last 7 days"])
    assert problems == []


@pytest.mark.parametrize("lang, seen, sources", [
    ("ru", "розлив, видели сегодня", {"menu": "✅меню", "checkin": "чекин", "manual": "✍️со слов (Ivan)", "shop": "🛒в магазине"}),
    ("en", "draft, seen today", {"menu": "✅menu", "checkin": "check-in", "manual": "✍️reported by Ivan", "shop": "🛒in the shop"}),
])
def test_price_and_serving_pills_and_the_seen_note_belong_to_their_place_block(open_page, lang, seen, sources):
    page, problems = open_page(layout_data(), width=375, query=f"?lang={lang}")
    assert page.evaluate("[...document.querySelectorAll('#rows .chip-serving, #rows .chip-note')].every((c) => c.closest('.place-block'))")
    assert page.evaluate("[...document.querySelectorAll('#rows .card > *:not(.place-blocks)')].every((c) => !c.querySelector('.chip-serving, .chip-note'))")
    blocks = page.evaluate("""() => [...document.querySelectorAll('#rows .card')].map((card) => ({
      title: card.querySelector('.card-name').textContent.trim(),
      blocks: [...card.querySelectorAll('.place-block')].map((b) => ({
        place: b.querySelector('.pb-head').children[1].textContent,
        source: (b.querySelector('.chip-source') || {}).textContent || null,
        serving: [...b.querySelectorAll('.chip-serving')].map((c) => c.textContent),
        note: [...b.querySelectorAll('.chip-note')].map((c) => [c.textContent, c.firstChild.textContent]),
        order: [...b.children].map((c) => c.getBoundingClientRect().top),
        map: !!b.querySelector('.map-link'), shopName: (b.querySelector('.shop-name') || {}).textContent || null,
      }))}))""")
    by_title = {card["title"]: card["blocks"] for card in blocks}
    multi = by_title["Multi Beer"]
    bar, shop = multi
    assert bar["place"].startswith("Gargoyle Bar") and shop["place"] == "Beer City"
    unit = "мл" if lang == "ru" else "ml"
    bottle, draft = ("бутылка", "розлив") if lang == "ru" else ("bottle", "draft")
    assert bar["serving"] == [f"{draft} · 2800 ֏", f"{bottle} 330 {unit} · 1500 ֏"]               # each pill is this place's own
    assert shop["serving"] == [f"{bottle} 330 {unit} · 1500 ֏"] and shop["source"] == sources["shop"]
    assert shop["shopName"] == ("в магазине: Multi Beer 0.33l" if lang == "ru" else "shop name: Multi Beer 0.33l")
    assert bar["source"] == sources["menu"] and bar["shopName"] is None
    stout = by_title["Dark Stout"][0]                                                             # a check-in: a note, no price
    assert stout["serving"] == [] and stout["source"] == sources["checkin"]
    assert stout["note"] == [["👀" + seen, "👀"]]                                                 # the eyes are inside the pill
    assert by_title["Friend Sour"][0]["source"] == sources["manual"]
    for card_blocks in by_title.values():
        for block in card_blocks:
            assert block["order"] == sorted(block["order"])                                       # top to bottom: head, pills, address, shop name
    assert problems == []


def test_a_shop_source_chip_links_to_the_shops_own_page_only_when_it_differs_from_the_beers(open_page):
    page, problems = open_page(layout_data(), width=375)
    links = page.eval_on_selector_all("#rows a.chip-source", "els => els.map((e) => [e.textContent, e.href, e.target, e.rel])")
    hrefs = sorted(link[1] for link in links)
    assert hrefs == ["https://shop.test/multi", "https://shop.test/pils"]
    assert all(link[2] == "_blank" and "noopener" in link[3] for link in links)
    assert page.eval_on_selector_all("#rows .chip-source:not(a)", "els => els.length") >= 6      # menu, check-in, manual: plain chips
    assert problems == []


def test_a_card_ends_with_when_the_beer_appeared_below_its_places(open_page):
    page, problems = open_page(layout_data(), width=375)
    feet = page.evaluate("""() => [...document.querySelectorAll('#rows .card')].map((card) => {
      const foot = card.querySelector('.card-foot'), blocks = card.querySelector('.place-blocks');
      return [foot.textContent, foot === card.lastElementChild, foot.getBoundingClientRect().top >= blocks.getBoundingClientRect().bottom - 0.5];
    })""")
    assert len(feet) == 12 and all(last and below for _, last, below in feet)
    assert all(re.fullmatch(r"Appeared \d\d\.\d\d, \d\d:\d\d", text) for text, _, _ in feet)
    assert problems == []


def test_every_link_in_a_card_has_a_44px_hit_area(open_page):
    page, problems = open_page(layout_data(), width=375)
    heights = page.eval_on_selector_all("#rows .card a", "els => els.map((e) => [e.className || e.parentElement.className, "
                                                         "parseFloat(getComputedStyle(e, '::after').height)])")
    assert len(heights) >= 15 and min(height for _, height in heights) >= 44, sorted(heights, key=lambda h: h[1])[:3]
    assert problems == []


@pytest.mark.parametrize("width", [320, 375])
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_no_card_overflows_on_a_phone_however_long_the_name_or_the_english(open_page, lang, width):
    page, problems = open_page(layout_data(), width=width, query=f"?lang={lang}")
    assert all(card["scroll"][0] <= card["scroll"][1] for card in cards(page).values())
    if width == 375:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert problems == []


TABLE_JS = """() => [...document.querySelectorAll('#rows tbody tr')].map((row) => {
  const cell = (i) => row.children[i];
  const rect = (e) => { const r = e.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height}; };
  const chip = row.querySelector('.chip-rating');
  return {
    title: row.querySelector('.beer').textContent.trim(),
    align: [3, 4, 5, 6].map((i) => getComputedStyle(cell(i - 1)).textAlign),
    figures: [3, 4, 5, 6].map((i) => getComputedStyle(cell(i - 1)).fontVariantNumeric),
    rating: chip ? rect(chip) : null,
    price: [...cell(5).querySelectorAll('.chip-serving')].map(rect),
    flagsIn: [...row.querySelectorAll('.chip-flag')].map((f) => f.closest('td').cellIndex),
    head: row.querySelector('.place-block .pb-head') ? rect(row.querySelector('.place-block .pb-head')) : null,
    name: row.querySelector('.place-block .pb-head').children[1].getBoundingClientRect().top,
    source: row.querySelector('.place-block .chip-source') ? row.querySelector('.place-block .chip-source').getBoundingClientRect().top : null,
    chips: [...row.querySelectorAll('.chip-serving, .chip-note, .chip-source')].map((c) => c.textContent).sort(),
  };
})"""


def test_table_numbers_are_right_aligned_in_tabular_figures_and_chips_share_their_edge(open_page):
    page, problems = open_page(layout_data(), width=1280)
    rows = {row["title"]: row for row in page.evaluate(TABLE_JS)}
    for row in rows.values():
        assert row["align"] == ["right"] * 4 and all("tabular-nums" in f for f in row["figures"]), row["title"]
    aligns = page.eval_on_selector_all("#rows thead th", "els => els.map((e) => getComputedStyle(e).textAlign)")
    assert aligns == ["left", "left", "right", "right", "right", "right", "left", "left"]
    rated = [row["rating"] for row in rows.values() if row["rating"]]
    assert len(rated) >= 3 and len({round(r["right"], 1) for r in rated}) == 1                  # one right edge, one size
    assert len({(round(r["width"], 1), round(r["height"], 1)) for r in rated}) == 1
    priced = [row["price"][0] for row in rows.values() if row["price"]]
    assert len(priced) >= 5 and len({round(p["right"], 1) for p in priced}) == 1
    assert problems == []


def test_a_table_place_block_keeps_its_source_on_the_line_of_the_place_name(open_page):
    page, problems = open_page(layout_data(), width=1280)
    rows = page.evaluate(TABLE_JS)
    # a long "reported by ..." chip may wrap under the name in a tight column, as it does on a phone; the others never do
    with_source = [row for row in rows if row["source"] is not None and row["title"] not in ("Friend Sour", "Anon Gose")]
    assert len(with_source) >= 8
    for row in with_source:
        assert abs(row["source"] - row["head"]["top"]) < 12, row["title"]                        # beside the name, not wrapped under it
        assert row["head"]["height"] < 34, row["title"]
    assert problems == []


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_the_flags_of_a_table_row_stand_beside_its_date_and_never_shift_the_name(open_page, lang):
    page, problems = open_page(layout_data(), width=1280, query=f"?lang={lang}")
    rows = {row["title"]: row for row in page.evaluate(TABLE_JS)}
    assert rows["Test IPA"]["flagsIn"] == [7, 7] and rows["Fresh Unrated"]["flagsIn"] == [7]
    assert rows["Dark Stout"]["flagsIn"] == []
    lefts = page.eval_on_selector_all("#rows tbody tr .beer", "els => els.map((e) => e.getBoundingClientRect().left)")
    assert len({round(left, 1) for left in lefts}) == 1                                          # every name starts in one place
    assert problems == []


def test_a_beer_carries_the_same_chips_in_the_table_and_in_its_card(open_page):
    """The table and the cards say the same about a beer: same sources, same servings, same seen-when notes."""
    wide, problems = open_page(layout_data(), width=1280)
    in_table = {row["title"]: row["chips"] for row in wide.evaluate(TABLE_JS)}
    narrow, more_problems = open_page(layout_data(), width=375)
    in_cards = narrow.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#rows .card')].map((card) => [
      card.querySelector('.card-name').textContent.trim(),
      [...card.querySelectorAll('.chip-serving, .chip-note, .chip-source')].map((c) => c.textContent).sort()]))""")
    assert in_table == in_cards and len(in_table) == 12
    assert problems == [] and more_problems == []


VENUES_JS = """() => [...document.querySelectorAll('#venues .card')].map((card) => {
  const rect = (selector) => {
    const e = card.querySelector(selector);
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, width: r.width, height: r.height};
  };
  const kinds = (parent) => [...parent.children].map((c) => c.classList[0]);
  return {parts: kinds(card), head: kinds(card.querySelector('.card-head')), box: rect('.card-head'), name: rect('.card-name'),
          logo: rect('.card-head > :first-child'), slot: rect('.rate-slot'), tag: rect('.tracked-badge'),
          title: card.querySelector('.card-name').textContent.trim(), scroll: [card.scrollWidth, card.clientWidth]};
})"""


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_a_venue_card_has_a_beer_cards_header_with_its_tag_pinned_to_the_top_right(open_page, lang):
    page, problems = open_page(width=375, query=f"?lang={lang}")
    page.click("#tab-venues")
    venues = page.evaluate(VENUES_JS)
    assert len(venues) == 6
    for venue in venues:
        assert venue["parts"] in (["card-head", "map-link", "meta", "meta"], ["card-head", "map-link", "meta"],   # an address line, or none:
                                  ["card-head", "meta", "meta"], ["card-head", "meta"])                        # the pin beside the name is the link
        assert venue["head"][:2] == ["avatar", "card-name"] and venue["head"][2:] in ([], ["rate-slot"])
        assert abs(venue["logo"]["width"] - 44) < 0.5 and abs(venue["logo"]["height"] - 44) < 0.5
        assert abs(venue["name"]["left"] - venue["box"]["left"] - 56) < 0.5                       # the name starts where a beer's does
        assert venue["scroll"][0] <= venue["scroll"][1]
        if venue["tag"]:                                                                          # listed: the tag, at the top right
            assert abs(venue["tag"]["right"] - venue["box"]["right"]) <= 0.5 and venue["name"]["right"] <= venue["slot"]["left"] - 11
        else:                                                                                     # not listed: no tag, no gap
            assert venue["slot"] is None and abs(venue["name"]["right"] - venue["box"]["right"]) <= 0.5
    assert [bool(venue["tag"]) for venue in venues] == [True, False, True, True, True, False]     # n % 2 == 1 is tracked
    assert len({round(venue["tag"]["width"], 1) for venue in venues if venue["tag"]}) == 1        # one size
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert problems == []


@pytest.mark.parametrize("width", [375, 1280])
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_the_map_pin_sits_on_the_line_of_the_place_name_with_a_44px_hit_area(open_page, lang, width):
    page, problems = open_page(layout_data(), width=width, query=f"?lang={lang}")
    where = "Открыть на Яндекс Картах: " if lang == "ru" else "Open in Yandex Maps: "
    blocks = page.evaluate("""() => [...document.querySelectorAll('#rows .place-block')].map((b) => {
      const r = (e) => { const x = e.getBoundingClientRect(); return {left: x.left, right: x.right, top: x.top, width: x.width, height: x.height}; };
      const pin = b.querySelector('.map-pin'), name = b.querySelector('.pb-name > :first-child'), line = b.querySelector('.map-link');
      return {name: name.textContent, pin: pin && r(pin), nameBox: r(name), label: pin && pin.getAttribute('aria-label'),
              title: pin && pin.title, line: line && line.textContent, head: r(b.querySelector('.pb-head'))};
    })""")
    pinned = [b for b in blocks if b["pin"]]
    assert "Beer City" in {b["name"] for b in blocks if not b["pin"]}                           # no map_url: no pin...
    assert all(b["line"] is None for b in blocks if not b["pin"])                               # ...and no line
    assert {b["name"] for b in pinned} >= {"Gargoyle Bar✓", "Beatles Pub"}
    for b in pinned:
        assert abs(b["pin"]["width"] - 44) < 0.5 and abs(b["pin"]["height"] - 44) < 0.5, b["name"]   # the hit area
        assert b["pin"]["left"] >= b["nameBox"]["right"] - 0.5, b["name"]                            # right after the name...
        assert abs((b["pin"]["top"] + 22) - (b["nameBox"]["top"] + b["nameBox"]["height"] / 2)) < 3, b["name"]   # ...on its line
        assert b["head"]["height"] < 34, b["name"]                                                    # the pin does not make the head taller
        assert b["label"].startswith(where)
    by_name = {b["name"]: b for b in pinned}
    assert by_name["Gargoyle Bar✓"]["title"] == "1 Test St" and by_name["Gargoyle Bar✓"]["line"] == "1 Test St"   # the address stays as a line
    assert by_name["Beatles Pub"]["line"] is None and by_name["Beatles Pub"]["title"] == ("на карте" if lang == "ru" else "on the map")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert problems == []


@pytest.mark.parametrize("tab", ["tab-all", "tab-bars", "tab-shops"])
def test_the_bars_and_shops_tabs_draw_the_same_cards_as_all_beer(open_page, tab):
    page, problems = open_page(layout_data(), width=375)
    page.click(f"#{tab}")
    shapes = page.evaluate(CARDS_JS)
    assert len(shapes) >= 3
    for shape in shapes:
        assert shape["parts"][0] == "card-head" and shape["parts"][-1] == "card-foot" and "place-blocks" in shape["parts"], shape["title"]
        assert shape["scroll"][0] <= shape["scroll"][1], shape["title"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert problems == []


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_a_phone_needs_no_sideways_scrolling_in_either_language(open_page, lang):
    page, problems = open_page(width=375, query=f"?lang={lang}")
    for tab in TABS:
        page.click(f"#{tab}")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), tab
    assert problems == []
