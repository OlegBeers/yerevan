"""The RU | EN interface of site/index.html, run for real: Chrome driven by Playwright, the page and its data.json served
from memory (no network). Skipped where neither the installed Chrome nor Playwright's Chromium can be launched."""
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
    for wanted in ("✅меню", "розлив, видели сегодня", "дегустационный, видели вчера", "из бочки, видели 5 дней назад",
                   "со слов (Ivan)", "🛒в магазине", "в магазине: Multi Beer 0.33l", "Чехия", "Freedonia", "Армения",
                   "розлив 2800 ֏ · бутылка 1500 ֏ 330 мл", "500 мл банка", "900 ֏"):
        assert wanted in rows, wanted
    assert titles(page, "#rows .flag") == ["возможно, впервые в Ереване", "новинка за 7 дней", "есть в меню"]
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
    assert text(page, "#venues li:nth-child(6) .map-link") == "на карте"
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
    for wanted in ("✅menu", "draft, seen today", "taster, seen yesterday", "cask, seen 5 days ago", "reported by Ivan",
                   "🛒in the shop", "shop name: Multi Beer 0.33l", "Czech Republic", "Freedonia", "Armenia",
                   "draft 2800 ֏ · bottle 1500 ֏ 330 ml", "500 ml can", "900 ֏"):
        assert wanted in rows, wanted
    assert titles(page, "#rows .flag") == ["possibly the first time in Yerevan", "new in the last 7 days", "on the menu"]
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
    assert text(page, "#venues li:nth-child(6) .map-link") == "on the map"
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


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_a_phone_needs_no_sideways_scrolling_in_either_language(open_page, lang):
    page, problems = open_page(width=375, query=f"?lang={lang}")
    for tab in TABS:
        page.click(f"#{tab}")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), tab
    assert problems == []
