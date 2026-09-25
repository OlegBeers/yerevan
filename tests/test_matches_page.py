"""Static checks of site/matches.html, the page where Oleg reviews shop -> Untappd merges."""
import re
from pathlib import Path

from bs4 import BeautifulSoup

PAGE = Path(__file__).resolve().parent.parent / "site" / "matches.html"
WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "run.yml"

MATCH_FIELDS = ("place_id", "place", "key", "shop_name", "shop_brewery", "shop_url", "shop_photo",
                "untappd_name", "untappd_brewery", "untappd_url", "untappd_logo", "rating", "via", "weak")


def _soup():
    return BeautifulSoup(PAGE.read_text(encoding="utf-8"), "html.parser")


def _js(soup):
    return "\n".join(s.get_text() for s in soup.find_all("script"))


def _css(soup):
    return "\n".join(s.get_text() for s in soup.find_all("style"))


def test_page_is_russian_and_mobile_ready():
    soup = _soup()
    assert soup.html["lang"] == "ru"
    assert soup.find("meta", attrs={"charset": True})["charset"].lower() == "utf-8"
    assert "width=device-width" in soup.find("meta", attrs={"name": "viewport"})["content"]
    assert soup.title.get_text() == "Проверка склеек"


def test_intro_explains_the_two_sides_and_what_to_mark():
    text = _soup().find("main").get_text(" ", strip=True)
    assert ("Слева — как пиво называется в магазине, справа — сорт на Untappd, с которым мы его склеили. "
            "Если это разные пивоварни или сорта — отметьте «не то пиво».") in text


def test_links_back_to_the_main_page_and_ships_with_the_published_site_folder():
    assert _soup().find("a", string=re.compile("На главную"))["href"] == "index.html"
    assert re.search(r"^\s*path:\s*site\s*$", WORKFLOW.read_text(encoding="utf-8"), re.M)


def test_required_elements_exist_and_controls_are_labelled():
    soup = _soup()
    for element_id in ("search", "only-weak", "count", "list", "bar", "marked", "copy"):
        assert soup.find(id=element_id) is not None, element_id
    for control in (soup.find(id="search"), soup.find(id="only-weak")):
        label = soup.find("label", attrs={"for": control["id"]}) or control.find_parent("label")
        assert label is not None and label.get_text(strip=True), control["id"]
    assert soup.find(id="copy").name == "button" and soup.find(id="copy")["type"] == "button"
    assert "Скопировать для Олега" in soup.find(id="copy").get_text()
    assert "Отмечено:" in soup.find(id="bar").get_text()
    assert "только слабые" in soup.find(id="only-weak").find_parent("label").get_text()


def test_no_external_scripts_and_dom_is_built_safely():
    soup = _soup()
    for script in soup.find_all("script"):
        assert not script.get("src"), script["src"]
    for link in soup.find_all("link", rel="stylesheet"):
        assert link["href"].startswith("https://fonts.googleapis.com/"), link["href"]
    js = _js(soup)
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
    assert "https?:" in js and "noopener" in js and "no-referrer" in js


def test_loads_data_json_bypassing_cache_and_uses_every_match_field():
    js = _js(_soup())
    assert re.search(r"""fetch\(\s*["']\./data\.json["']\s*,\s*\{\s*cache:\s*["']no-store["']\s*\}\s*\)""", js)
    for field in MATCH_FIELDS + ("matches",):
        assert re.search(rf"\.{field}\b", js), field


def test_how_a_match_was_found_is_labelled_in_russian_and_weak_ones_are_flagged():
    js = _js(_soup())
    for text in ("авто по названию", "поиск Untappd", "вручную", "⚠️", "слабое совпадение", "не то пиво"):
        assert text in js or text in PAGE.read_text(encoding="utf-8"), text
    assert re.search(r"local:\s*[\"']авто по названию[\"']", js)
    assert re.search(r"search:\s*[\"']поиск Untappd[\"']", js)
    assert re.search(r"manual:\s*[\"']вручную[\"']", js)


def test_marks_are_kept_in_local_storage_guarded_by_try_catch():
    js = _js(_soup())
    assert "localStorage.getItem" in js and "localStorage.setItem" in js
    for call in ("getItem", "setItem"):
        idx = js.index(f"localStorage.{call}")
        before = js[:idx]
        assert before.rfind("try {") > before.rfind("catch"), call   # the call sits inside a try block


def test_copy_text_has_one_line_per_marked_entry_in_the_agreed_format():
    js = _js(_soup())
    fn = re.search(r"function copyText\(\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert re.search(r"\$\{m\.place_id\} \| \$\{m\.key\} \| \$\{m\.shop_name\} → \$\{m\.untappd_name[^}]*\} \(\$\{m\.untappd_url[^}]*\}\)", fn)
    assert '.join("\\n")' in fn


def test_copy_uses_the_clipboard_api_with_a_textarea_fallback():
    js = _js(_soup())
    assert "navigator.clipboard" in js and "writeText" in js
    assert 'el("textarea"' in js and ".select()" in js and 'execCommand("copy")' in js


def test_filters_only_weak_and_text_search():
    js = _js(_soup())
    fn = re.search(r"function visibleMatches\(\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "only-weak" in fn and ".weak" in fn
    assert '$("search")' in fn and "toLowerCase" in fn


def test_bottom_bar_is_sticky_and_the_page_never_scrolls_sideways():
    css = _css(_soup())
    assert re.search(r"#bar\s*\{[^}]*position:\s*sticky[^}]*bottom:\s*0", css, re.S)
    assert re.search(r"overflow-wrap:\s*anywhere", css)
    assert re.search(r"@media \(max-width: \d+px\)\s*\{[^}]*grid-template-columns:\s*1fr\b", css, re.S)


def test_colours_are_custom_properties_with_dark_variant():
    css = _css(_soup())
    root = re.search(r":root\s*\{([^}]*)\}", css).group(1)
    assert "--bg:" in root and "--text:" in root and "--accent:" in root and "--danger:" in root
    dark = css.split("prefers-color-scheme: dark", 1)[1]
    assert "--bg:" in dark and "--accent:" in dark
