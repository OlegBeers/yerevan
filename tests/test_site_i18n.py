"""Static checks of the RU | EN interface of site/index.html: the dictionaries, the switch and how the language is chosen.

The page's behaviour in a real browser is checked in test_site_i18n_browser.py."""
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Comment

PAGE = Path(__file__).resolve().parent.parent / "site" / "index.html"

CYRILLIC = re.compile(r"[Ѐ-ӿ]")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
ATTRS = ("aria-label", "placeholder", "title", "content")   # attributes the markup lets the dictionary fill
SAME_IN_BOTH_LANGUAGES = {"th.ibu"}                          # "IBU" is IBU everywhere


def _soup():
    return BeautifulSoup(PAGE.read_text(encoding="utf-8"), "html.parser")


def _js(soup=None):
    return "\n".join(s.get_text() for s in (soup or _soup()).find_all("script"))


def _texts(value):
    return list(value.values()) if isinstance(value, dict) else [value]


def _dictionaries():
    """{"ru": {key: str | {form: str}}, "en": ...} read out of the `const I18N = {...}` literal, which keeps
    one `"key": value,` entry per line so that it can be read without a JavaScript engine."""
    block = re.search(r"^const I18N = \{\n(.*?)^\};$", _js(), re.S | re.M)
    assert block, "the `const I18N = {` dictionary is missing"
    out = {}
    for lang, body in re.findall(r"^  (\w+): \{\n(.*?)^  \},$", block.group(1), re.S | re.M):
        entries = {}
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            m = re.fullmatch(r'"([\w.]+)": (.+),', line)
            assert m, f"{lang}: not a one-line `\"key\": value,` entry: {line!r}"
            key, raw = m.groups()
            assert key not in entries, f"{lang}: {key} is defined twice"
            entries[key] = json.loads(raw) if raw.startswith('"') else {
                form: json.loads(text) for form, text in re.findall(r'(\w+): ("(?:[^"\\]|\\.)*")', raw)}
        out[lang] = entries
    return out


def _function_body(js, header):
    match = re.search(rf"{header}\s*\{{(.*?)\n\}}", js, re.S)
    assert match, header
    return match.group(1)


def test_russian_and_english_dictionaries_have_exactly_the_same_keys():
    dictionaries = _dictionaries()
    assert list(dictionaries) == ["ru", "en"]
    ru, en = dictionaries["ru"], dictionaries["en"]
    assert set(ru) - set(en) == set(), "keys missing from English"
    assert set(en) - set(ru) == set(), "keys missing from Russian"
    assert len(ru) > 60


def test_placeholders_and_plural_forms_agree_between_the_languages():
    ru, en = _dictionaries()["ru"], _dictionaries()["en"]
    for key in ru:
        assert isinstance(ru[key], dict) == isinstance(en[key], dict), key
        if isinstance(ru[key], dict):
            assert set(ru[key]) == {"one", "few", "many"}, key   # Russian: 1 день, 2 дня, 5 дней
            assert set(en[key]) == {"one", "other"}, key         # English: 1 day, 2 days
        else:
            assert set(PLACEHOLDER.findall(ru[key])) == set(PLACEHOLDER.findall(en[key])), key


def test_english_texts_are_translated_and_free_of_cyrillic():
    ru, en = _dictionaries()["ru"], _dictionaries()["en"]
    for key, value in en.items():
        assert not any(CYRILLIC.search(text) for text in _texts(value)), key
        if key not in SAME_IN_BOTH_LANGUAGES:
            assert value != ru[key], f"{key} is not translated"


def test_russian_dictionary_keeps_the_wording_the_page_has_always_had():
    ru = _dictionaries()["ru"]
    for key, text in {
        "tab.all": "Всё пиво", "tab.venues": "Где пьют", "sort.since": "по новизне",
        "only.new": "только новинки за 7 дней", "th.since": "Появилось", "count.all": "Позиций: {total}",
        "count.some": "Показано {shown} из {total}", "banner.stale.at": "⚠️ Данные устарели: последнее обновление {dm} в {hm}.",
        "legend.star.since": "⭐ — возможно, впервые в Ереване (с тех пор, как следим, с {dm})",
        "place.ok": "проверено {when} в {hm}", "updated.hours": "обновлено {hours} ч назад",
    }.items():
        assert ru[key] == text, key
    assert ru["unit.day"] == {"one": "день", "few": "дня", "many": "дней"}
    assert ru["unit.checkin"] == {"one": "чекин", "few": "чекина", "many": "чекинов"}


def test_static_markup_holds_the_russian_text_the_dictionary_has_under_its_key():
    """The Russian text stays in the HTML (no blank page before the script runs); a key that drifts from it fails here."""
    soup, ru = _soup(), _dictionaries()["ru"]
    keyed = soup.find_all(attrs={"data-i18n": True})
    assert len(keyed) > 25
    for node in keyed:
        assert node["data-i18n"] in ru, node["data-i18n"]
        assert " ".join(node.get_text().split()) == ru[node["data-i18n"]], node["data-i18n"]
    for attr in ATTRS:
        for node in soup.find_all(attrs={f"data-i18n-{attr}": True}):
            assert node[attr] == ru[node[f"data-i18n-{attr}"]], (attr, node[f"data-i18n-{attr}"])


def test_no_russian_text_in_the_markup_is_left_outside_the_dictionary():
    soup = _soup()
    for text in soup.find_all(string=CYRILLIC):
        if isinstance(text, Comment) or text.find_parent(["script", "style"]):
            continue
        assert text.find_parent(attrs={"data-i18n": True}), f"cannot be translated: {text.strip()!r}"
    for tag in soup.find_all(True):
        if tag.has_attr("data-lang"):   # the switch names each language in its own language, in both
            continue
        for attr in ATTRS:
            if CYRILLIC.search(tag.get(attr) or ""):
                assert tag.has_attr(f"data-i18n-{attr}"), f"{tag.name} {attr}={tag[attr]!r}"


def test_the_page_title_description_and_headline_are_translatable():
    soup = _soup()
    assert soup.title["data-i18n"] == "title"
    assert soup.find("meta", attrs={"name": "description"})["data-i18n-content"] == "meta.description"
    assert soup.find("h1").find(attrs={"data-i18n": "title"}) is not None


def test_language_switch_is_a_pair_of_toggle_buttons_in_the_header():
    header = _soup().find("header")
    group = header.find(class_="lang")
    assert group["role"] == "group" and group["data-i18n-aria-label"] == "lang.label" and group["aria-label"]
    buttons = group.find_all("button")
    assert [b["data-lang"] for b in buttons] == ["ru", "en"]
    assert [b.get_text() for b in buttons] == ["RU", "EN"]
    assert all(b["type"] == "button" for b in buttons)
    assert [b["aria-pressed"] for b in buttons] == ["true", "false"]   # the markup is Russian until the script says otherwise
    assert [b["title"] for b in buttons] == ["Русский", "English"]


def test_the_switch_is_small_and_takes_its_colours_from_the_theme_tokens():
    css = "\n".join(s.get_text() for s in _soup().find_all("style"))
    rule = re.search(r"\.lang button\[aria-pressed=\"true\"\]\s*\{([^}]*)\}", css).group(1)
    assert "var(--accent)" in rule and "var(--on-accent)" in rule
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", rule)


def test_language_is_the_url_choice_then_the_saved_one_then_the_browsers():
    js = _js()
    assert re.search(r'const LANGS = Object\.keys\(I18N\);', js)
    fn = _function_body(js, r"function initialLang\(\)")
    assert 'new URLSearchParams(location.search).get("lang")' in fn
    assert fn.index("URLSearchParams") < fn.index("localStorage.getItem") < fn.index("navigator.language")
    assert "LANGS.includes(" in fn                       # only a language the page has, never an arbitrary string
    assert re.search(r'if \(LANGS\.includes\(asked\)\) \{ saveLang\(asked\);', fn)   # ?lang=... is saved as well
    assert re.search(r'navigator\.language.*split\("-"\)\[0\] === "ru" \? "ru" : "en"', fn)   # ru, ru-RU -> ru; else en


def test_local_storage_is_only_touched_inside_try_catch():
    js = _js()
    for header in (r"function saveLang\(lang\)", r"function initialLang\(\)"):
        body = _function_body(js, header)
        assert re.search(r"try \{[^}]*localStorage[^}]*\} catch", body, re.S), header
    assert 'const LANG_KEY = "yerevan.lang"' in js   # namespaced: the origin is shared with other pages of the account


def test_switching_repaints_in_place_and_leaves_filters_and_section_alone():
    js = _js()
    fn = _function_body(js, r"function setLang\(lang\)")
    for call in ("applyLang()", "renderHeader()", "renderPlaces()", "render()", "renderVenues()"):
        assert call in fn, call
    assert "setSection(" not in fn and "location.reload" not in fn and "ui.place =" not in fn
    assert "ui.failed" in fn                            # an error message is translated too
    apply_fn = _function_body(js, r"function applyLang\(\)")
    assert "document.documentElement.lang = ui.lang" in apply_fn
    assert 'querySelectorAll("[data-i18n]")' in apply_fn and "textContent = t(" in apply_fn
    assert 'b.setAttribute("aria-pressed", String(b.dataset.lang === ui.lang))' in apply_fn
    assert re.search(r'for \(const b of document\.querySelectorAll\("\.lang button"\)\) b\.addEventListener\("click", '
                     r'\(\) => setLang\(b\.dataset\.lang\)\);', js)


def test_a_shared_language_link_follows_the_switch_so_a_reload_keeps_the_choice():
    fn = _function_body(_js(), r"function setLang\(lang\)")
    assert 'url.searchParams.has("lang")' in fn and "history.replaceState(" in fn


def test_the_script_initialises_the_language_before_it_loads_the_data():
    js = _js()
    start = js.rindex("ui.lang = initialLang();")
    assert start < js.rindex("applyLang();") < js.rindex("\nload();")
