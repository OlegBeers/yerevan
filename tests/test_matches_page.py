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
            "Если склейка правильная — отметьте «верно», она уйдёт из списка. "
            "Если это разные пивоварни или сорта — отметьте «не то пиво»") in text


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


def test_confirmed_matches_are_hidden_by_default_and_can_be_shown():
    soup = _soup()
    assert soup.find(id="show-ok") is not None
    assert "проверенные" in soup.find(id="show-ok").find_parent("label").get_text()
    js = _js(soup)
    fn = re.search(r"function visibleMatches\(\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "show-ok" in fn and "isOk" in fn


def test_confirmation_is_tied_to_the_untappd_link_so_a_changed_match_is_reviewed_again():
    js = _js(_soup())
    fn = re.search(r"function isOk\(m\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "untappd_url" in fn
    assert 'localStorage.getItem(OK_KEY' in js and 'localStorage.setItem(OK_KEY' in js


def test_each_card_has_right_and_wrong_marks_that_exclude_each_other_and_a_bulk_confirm_exists():
    soup = _soup()
    js = _js(soup)
    card_fn = re.search(r"function cardNode\(m\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "верно" in card_fn and "не то пиво" in card_fn
    assert soup.find(id="confirm-visible").name == "button"
    assert "confirm(" in js                                  # asks before confirming everything shown
    assert "Осталось проверить" in js


def test_marks_can_be_moved_to_another_device_through_a_link():
    soup = _soup()
    assert soup.find(id="transfer").name == "button"
    assert "другое устройство" in soup.find(id="transfer").get_text()
    js = _js(soup)
    assert re.search(r"function transferLink\(\)", js) and "#marks=" in js and "btoa" in js
    import_fn = re.search(r"function importFromHash\(\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "location.hash" in import_fn and "fromB64(" in import_fn and "try {" in import_fn   # a broken link must not break the page
    assert "atob(" in js
    assert "history.replaceState" in js                       # the marks leave the address bar after loading
    assert "importFromHash();" in js.split("async function load()")[1]


def test_a_mode_switch_opens_the_merge_panel_and_puts_the_review_flow_in_a_panel_of_its_own():
    soup = _soup()
    review_tab, merge_tab = soup.find(id="mode-review"), soup.find(id="mode-merge")
    for tab in (review_tab, merge_tab):
        assert tab.name == "button" and tab["type"] == "button", tab["id"]
    assert (review_tab["aria-pressed"], merge_tab["aria-pressed"]) == ("true", "false")
    assert review_tab.get_text(strip=True) == "Проверка" and merge_tab.get_text(strip=True) == "Объединить пиво"
    review, merge = soup.find(id="review"), soup.find(id="merge")
    assert merge.has_attr("hidden") and not review.has_attr("hidden")
    for element_id in ("search", "only-weak", "show-ok", "count", "list"):
        assert review.find(id=element_id) is not None and merge.find(id=element_id) is None, element_id
    js = _js(soup)
    fn = re.search(r"function setMode\(mode\)\s*\{(.*?)\n\}", js, re.S).group(1)
    for element_id in ("review", "merge", "bar"):   # the bar of marks belongs to the review list, so it goes with it
        assert f'$("{element_id}").hidden' in fn, element_id
    assert '$("mode-merge").addEventListener("click"' in js


def test_merge_panel_searches_shop_items_and_keeps_the_chosen_ones_in_a_sticky_sheet():
    soup = _soup()
    merge = soup.find(id="merge")
    search = merge.find(id="pick-search")
    assert search["type"] == "search" and search["autocomplete"] == "off"
    label = soup.find("label", attrs={"for": "pick-search"})
    assert label is not None and label.get_text(strip=True)
    for element_id in ("pick-count", "pick-list", "sheet", "picked-count", "picked-list"):
        assert merge.find(id=element_id) is not None, element_id
    assert "Выбрано:" in merge.find(id="sheet").get_text()
    js = _js(soup)
    items_fn = re.search(r"function shopItems\(data\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert 'startsWith("n:")' in items_fn          # same_as takes shop names (n:) only
    assert "data.rows" in items_fn and "data.places" in items_fn
    assert "PICK_LIMIT" in js
    css = _css(soup)
    assert re.search(r"#bar\s*\{[^}]*position:\s*sticky[^}]*bottom:\s*0", css, re.S)
    assert re.search(r"#sheet[^{]*\{[^}]*position:\s*sticky[^}]*bottom:\s*0", css, re.S)


def test_the_untappd_link_field_is_a_big_url_field_and_the_number_is_read_with_a_strict_pattern():
    soup = _soup()
    field = soup.find(id="untappd-link")
    assert field.name == "input" and field["type"] == "url" and field["inputmode"] == "url" and field["autocomplete"] == "off"
    label = soup.find("label", attrs={"for": "untappd-link"})
    assert label is not None and label.get_text(strip=True) == "Ссылка на пиво в Untappd"
    assert soup.find(id="link-status")["role"] == "status"
    js = _js(soup)
    assert r"/untappd\.com\/(?:b\/[^\/\s]+\/|beer\/)(\d+)/" in js
    fn = re.search(r"function renderLink\(\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "safeUrl(" in fn and "noopener" in fn and "noreferrer" in fn      # the link to the beer goes through the same checks
    assert soup.find(id="to-link").name == "button" and "Дальше" in soup.find(id="to-link").get_text()
