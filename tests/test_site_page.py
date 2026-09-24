import re
from pathlib import Path

from bs4 import BeautifulSoup

PAGE = Path(__file__).resolve().parent.parent / "site" / "index.html"

ROW_FIELDS = ("place_id", "section", "name", "brewery", "style", "abv", "ibu", "rating", "price_amd",
              "volume_ml", "container", "badge", "since", "seen_days_ago", "new", "star", "url", "by")
PLACE_FIELDS = ("id", "name", "section", "last_ok", "menu_updated_at", "failing", "failing_days")


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
    for element_id in ("tab-bars", "tab-shops", "only-new", "sort", "search", "places", "count", "rows",
                       "stale-banner", "updated", "legend-star", "legend-hot"):
        assert soup.find(id=element_id) is not None, element_id


def test_tabs_are_toggle_buttons_with_bars_selected():
    soup = _soup()
    bars, shops = soup.find(id="tab-bars"), soup.find(id="tab-shops")
    assert bars.name == shops.name == "button"
    assert "🍻 Бары" in bars.get_text() and "🛒 Магазины" in shops.get_text()
    assert (bars["data-section"], shops["data-section"]) == ("bars", "shops")
    assert (bars["aria-pressed"], shops["aria-pressed"]) == ("true", "false")


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


def test_no_external_scripts_or_styles():
    soup = _soup()
    for script in soup.find_all("script"):
        src = script.get("src", "")
        assert not re.match(r"(?i)(https?:)?//", src), src
    for link in soup.find_all("link", rel="stylesheet"):
        assert link["href"].startswith("https://fonts.googleapis.com/"), link["href"]


def test_script_uses_every_data_field():
    js = _js(_soup())
    for field in ROW_FIELDS + PLACE_FIELDS + ("generated_at", "started_at", "hot_rating", "rows", "places"):
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
    for text in ("✅ меню", "👀 видели", "✍️ со слов", "🛒 в магазине", "🔥", "⭐", "🆕",
                 "меню обновлено", "проверено", "⚠️ не удалось проверить", "обновлено",
                 "ч назад", "Данные устарели", "с тех пор, как следим, с "):
        assert text in js, text
    for word in ("день", "дня", "дней"):
        assert f'"{word}"' in js, word


def test_colours_are_custom_properties_with_dark_variant():
    css = _css(_soup())
    root = re.search(r":root\s*\{([^}]*)\}", css).group(1)
    assert "--bg:" in root and "--text:" in root and "--warn-bg:" in root
    dark = css.split("prefers-color-scheme: dark", 1)[1]
    assert "--bg:" in dark and "--warn-bg:" in dark
    assert "overflow-x: auto" in css  # wide table scrolls inside its box, never the page


def test_footer_sources_legend_and_credits():
    soup = _soup()
    footer = soup.find("footer")
    text = footer.get_text(" ", strip=True)
    assert "Идея и основа — hopandshot.github.io/hopsandshot" in text
    assert "нашли ошибку — напишите олегу" in text.lower()
    assert soup.find(id="legend-star").get_text().startswith("⭐ — возможно, впервые в Ереване (с тех пор, как следим")
    hrefs = " ".join(a["href"] for a in footer.find_all("a"))
    for host in ("hopandshot.github.io/hopsandshot", "untappd.com", "buy.am", "beer-city.am",
                 "yerevan-city.am", "parma.am"):
        assert host in hrefs, host
