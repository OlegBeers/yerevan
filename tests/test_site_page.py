import re
from pathlib import Path

from bs4 import BeautifulSoup

PAGE = Path(__file__).resolve().parent.parent / "site" / "index.html"

ROW_FIELDS = ("place_id", "section", "name", "brewery", "style", "abv", "ibu", "rating", "price_amd",
              "volume_ml", "container", "badge", "since", "seen_days_ago", "new", "star", "url", "by", "serving")
PLACE_FIELDS = ("id", "name", "section", "last_ok", "menu_updated_at", "failing", "failing_days",
                "logo", "verified", "untappd_url", "addresses")
VENUE_FIELDS = ("name", "url", "logo", "verified", "checkins_30d", "last_checkin", "tracked")


def _soup():
    return BeautifulSoup(PAGE.read_text(encoding="utf-8"), "html.parser")


def _js(soup):
    return "\n".join(s.get_text() for s in soup.find_all("script"))


def _css(soup):
    return "\n".join(s.get_text() for s in soup.find_all("style"))


def _label_text(soup, control):
    """Accessible name of a form control: aria-label, <label for=id> or wrapping <label>."""
    if control.get("aria-label"):
        return control["aria-label"]
    label = soup.find("label", attrs={"for": control.get("id")}) or control.find_parent("label")
    return label.get_text(" ", strip=True) if label else ""


def test_page_is_russian_and_mobile_ready():
    soup = _soup()
    assert soup.html["lang"] == "ru"
    assert soup.find("meta", attrs={"charset": True})["charset"].lower() == "utf-8"
    assert "width=device-width" in soup.find("meta", attrs={"name": "viewport"})["content"]


def test_required_elements_exist():
    soup = _soup()
    for element_id in ("tab-bars", "tab-shops", "tab-venues", "only-new", "sort", "search", "controls",
                       "places", "count", "rows", "venues-section", "venues", "stale-banner", "updated",
                       "legend-star", "legend-hot"):
        assert soup.find(id=element_id) is not None, element_id


def test_tabs_are_toggle_buttons_with_bars_selected():
    soup = _soup()
    bars, shops, venues = soup.find(id="tab-bars"), soup.find(id="tab-shops"), soup.find(id="tab-venues")
    assert bars.name == shops.name == venues.name == "button"
    assert "🍻" in bars.get_text() and "Бары" in bars.get_text()
    assert "🛒" in shops.get_text() and "Магазины" in shops.get_text()
    assert "📍" in venues.get_text() and "Все места" in venues.get_text()
    assert (bars["data-section"], shops["data-section"], venues["data-section"]) == ("bars", "shops", "venues")
    assert (bars["aria-pressed"], shops["aria-pressed"], venues["aria-pressed"]) == ("true", "false", "false")


def test_venues_section_starts_hidden():
    soup = _soup()
    assert soup.find(id="venues-section").has_attr("hidden")
    intro = soup.find(id="venues-section").find("p", class_="intro")
    assert "напишите Олегу" in intro.get_text()


def test_sort_select_defaults_to_newness():
    soup = _soup()
    options = soup.find("select", id="sort").find_all("option")
    assert [o["value"] for o in options] == ["since", "rating", "abv"]
    assert [o.has_attr("selected") for o in options] == [True, False, False]
    texts = " ".join(o.get_text() for o in options)
    assert "новизне" in texts and "рейтингу" in texts and "крепости" in texts


def test_only_new_toggle_is_a_checkbox():
    soup = _soup()
    toggle = soup.find(id="only-new")
    assert toggle.name == "input" and toggle["type"] == "checkbox"
    assert "🆕 только новинки за 7 дней" in _label_text(soup, toggle)


def test_every_form_control_has_an_accessible_name():
    soup = _soup()
    controls = soup.find_all(["input", "select"])
    assert {c["id"] for c in controls} == {"search", "sort", "only-new"}
    for control in controls:
        assert _label_text(soup, control), control["id"]
    assert soup.find(id="places")["aria-label"]


def test_stale_banner_hidden_until_script_decides():
    banner = _soup().find(id="stale-banner")
    assert banner.has_attr("hidden")
    assert "данные устарели" in banner.get_text().lower()


def test_loads_data_json_bypassing_cache():
    js = _js(_soup())
    assert re.search(r"""fetch\(\s*["']\./data\.json["']\s*,\s*\{\s*cache:\s*["']no-store["']\s*\}\s*\)""", js)


def test_load_ends_by_setting_the_current_section_so_a_pre_click_venues_tab_still_renders():
    """M-2: clicking the venues tab before data.json loads must not leave that tab blank."""
    js = _js(_soup())
    load_body = re.search(r"async function load\(\)\s*\{(.*?)\n\}", js, re.S).group(1)
    tail = [line for line in load_body.strip().splitlines() if line.strip()][-1]
    assert re.search(r"setSection\(\s*ui\.section\s*\)\s*;?", tail)


def test_no_external_scripts_or_styles():
    soup = _soup()
    for script in soup.find_all("script"):
        src = script.get("src", "")
        assert not re.match(r"(?i)(https?:)?//", src), src
    for link in soup.find_all("link", rel="stylesheet"):
        assert link["href"].startswith("https://fonts.googleapis.com/"), link["href"]


def test_script_uses_every_data_field():
    js = _js(_soup())
    for field in set(ROW_FIELDS + PLACE_FIELDS + VENUE_FIELDS) | {"generated_at", "started_at", "hot_rating",
                                                                     "rows", "places", "venues"}:
        assert re.search(rf"\.{field}\b", js), field


def test_script_builds_dom_safely():
    js = _js(_soup())
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
    assert "https?:" in js  # only http(s) links from data.json become href
    assert "noopener" in js


def test_script_texts_and_rules():
    js = _js(_soup())
    assert "Asia/Yerevan" in js
    assert re.search(r"STALE_HOURS\s*=\s*36\b", js)
    assert "min-width: 700px" in js
    for text in ("✅", "меню", "👀", "видели", "✍️", "со слов", "🛒", "в магазине", "🔥", "⭐", "🆕",
                 "меню обновлено", "проверено", "не удалось проверить", "обновлено",
                 "ч назад", "Данные устарели", "с тех пор, как следим, с ",
                 "Появилось", "за 60 дней", "в списке", "Верифицирован в Untappd", "no-referrer"):
        assert text in js, text
    assert "Замечено" not in js
    for word in ("день", "дня", "дней"):
        assert f'"{word}"' in js, word
    for word in ("чекин", "чекина", "чекинов"):
        assert f'"{word}"' in js, word


def test_icons_have_a_spacing_class_separate_from_text():
    """Every leading emoji/badge icon is its own element with a CSS gap, not glued text."""
    soup = _soup()
    css = _css(soup)
    js = _js(soup)
    assert re.search(r"\.ico\s*\{[^}]*margin-right", css)
    for tab_id in ("tab-bars", "tab-shops", "tab-venues"):
        ico = soup.find(id=tab_id).find("span", class_="ico")
        assert ico is not None, tab_id
    toggle_ico = soup.find(id="only-new").find_next_sibling("span", class_="ico")
    assert toggle_ico is not None
    assert "iconSpan" in js or "class: \"ico\"" in js


def test_places_scroll_horizontally_on_narrow_screens():
    css = _css(_soup())
    idx = css.find("max-width: 699px")
    assert idx != -1, "no <700px media query found for .places"
    block = css[idx:idx + 400]
    assert "overflow-x: auto" in block
    assert "flex-wrap: nowrap" in block
    assert "scroll-snap" in block


def test_tabs_dont_wrap_on_narrow_screens():
    """The three tab labels (e.g. "Магазины") must not wrap letter-by-letter at 360-375px."""
    css = _css(_soup())
    idx = css.find("max-width: 400px")
    assert idx != -1, "no narrow-screen media query found for .tab"
    block = css[idx:idx + 200]
    assert "white-space: nowrap" in block


def test_colours_are_custom_properties_with_dark_variant():
    css = _css(_soup())
    root = re.search(r":root\s*\{([^}]*)\}", css).group(1)
    assert "--bg:" in root and "--text:" in root and "--warn-bg:" in root
    dark = css.split("prefers-color-scheme: dark", 1)[1]
    assert "--bg:" in dark and "--warn-bg:" in dark
    assert "overflow-x: auto" in css  # wide table scrolls inside its box, never the page


def test_table_headers_use_new_since_label():
    js = _js(_soup())
    assert '"Появилось"' in js
    assert '"Замечено"' not in js


def test_footer_sources_legend_and_credits():
    soup = _soup()
    footer = soup.find("footer")
    text = footer.get_text(" ", strip=True)
    assert "Идея и основа" not in text
    assert "Спасибо Ивану за идею." in text
    assert "Проект делает и поддерживает Олег" in text
    assert "Пожелания и ошибки — туда." in text
    assert soup.find(id="legend-star").get_text().startswith("⭐ — возможно, впервые в Ереване (с тех пор, как следим")
    hrefs = " ".join(a["href"] for a in footer.find_all("a"))
    for host in ("untappd.com", "buy.am", "beer-city.am", "yerevan-city.am", "parma.am", "t.me/oleg_sorokin"):
        assert host in hrefs, host
