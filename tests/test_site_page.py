import re
from pathlib import Path

from bs4 import BeautifulSoup

PAGE = Path(__file__).resolve().parent.parent / "site" / "index.html"

ROW_FIELDS = ("place_id", "section", "name", "brewery", "style", "abv", "ibu", "rating", "price_amd",
              "volume_ml", "container", "badge", "since", "seen_days_ago", "new", "star", "url", "by", "serving",
              "beer_logo", "shop_url", "beer_key", "shop_name", "servings", "country", "style_inferred")
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


def test_tabs_are_toggle_buttons_with_all_beer_selected():
    soup = _soup()
    everything, bars, shops, venues = (soup.find(id=i) for i in ("tab-all", "tab-bars", "tab-shops", "tab-venues"))
    assert everything.name == bars.name == shops.name == venues.name == "button"
    assert "🔎" in everything.get_text() and "Всё пиво" in everything.get_text()
    assert "🍻" in bars.get_text() and "Бары" in bars.get_text()
    assert "🛒" in shops.get_text() and "Магазины" in shops.get_text()
    assert "📍" in venues.get_text() and "Где пьют" in venues.get_text()
    assert [t["data-section"] for t in (everything, bars, shops, venues)] == ["all", "bars", "shops", "venues"]
    assert [t["aria-pressed"] for t in (everything, bars, shops, venues)] == ["true", "false", "false", "false"]


def test_all_tab_searches_bars_and_shops_together():
    js = _js(_soup())
    assert 'section: "all"' in js
    assert re.search(r'function inSection\(r\)\s*\{[^}]*ui\.section === "all"', js)


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
    """The four tab labels (e.g. "Магазины") must not wrap letter-by-letter at 360-375px."""
    css = _css(_soup())
    idx = css.find("max-width: 560px")
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


def test_rows_are_filtered_before_being_grouped_by_beer():
    """Search/place/new filters apply to raw rows first; grouping by beer happens after."""
    js = _js(_soup())
    assert re.search(r"function groupBeers\(rows\)", js)
    assert re.search(r"groupBeers\(\s*filteredRows\(\)\s*\)", js)
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "beer_key" in group_fn


def test_beer_group_key_falls_back_to_normalized_brewery_and_name():
    js = _js(_soup())
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "r.beer_key" in group_fn
    assert "brewery" in group_fn and "toLowerCase" in group_fn


def test_group_since_is_earliest_and_rating_abv_use_best_value():
    js = _js(_soup())
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "sort()[0]" in group_fn  # earliest "since" among the grouped rows
    assert re.search(r'bestOf\(group,\s*"rating"\)', group_fn)
    assert re.search(r'bestOf\(group,\s*"abv"\)', group_fn)
    assert re.search(r"group\.some\(.*\.new\)", group_fn)
    assert re.search(r"group\.some\(.*\.star\)", group_fn)


def test_multi_place_beers_list_every_place_with_its_own_price_and_link():
    js = _js(_soup())
    assert re.search(r"function placeLines\(rows, name\)", js)
    place_lines_fn = re.search(r"function placeLines\(rows, name\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "badgeNodes" in place_lines_fn
    assert "priceText" in place_lines_fn and "volumeText" in place_lines_fn
    assert "shopLinkNode" in place_lines_fn
    # a beer with more than one place uses placeLines; a single-place beer keeps the old layout
    assert re.search(r"g\.rows\.length === 1", js) or re.search(r"g\.rows\.length !== 1", js)


def test_single_place_beers_keep_todays_layout():
    """Beers found at only one place still render through the pre-grouping helpers unchanged."""
    js = _js(_soup())
    assert "function placeCell(r, name)" in js
    assert "function cardWhere(r, name)" in js


def test_wherelist_css_stacks_multiple_place_lines():
    css = _css(_soup())
    assert re.search(r"\.wherelist\s*\{[^}]*flex-direction:\s*column", css)


def test_footer_sources_legend_and_credits():
    soup = _soup()
    footer = soup.find("footer")
    text = footer.get_text(" ", strip=True)
    assert "Идея и основа" not in text
    assert "Спасибо Ивану за идею." in text
    assert "Сайт и бота собрал и ведёт Олег." in text
    assert "пишите в Telegram" in text
    assert "Подписывайтесь на меня в Untappd: Oleg_Sorokin" in text
    assert soup.find(id="legend-star").get_text().startswith("⭐ — возможно, впервые в Ереване (с тех пор, как следим")
    hrefs = " ".join(a["href"] for a in footer.find_all("a"))
    for host in ("untappd.com", "buy.am", "beer-city.am", "yerevan-city.am", "parma.am", "t.me/oleg_sorokin",
                 "untappd.com/user/Oleg_Sorokin"):
        assert host in hrefs, host


def test_group_fields_prefer_the_untappd_native_row():
    """v1.2 beer identity: a group's display name/brewery/style/logo come from a row whose own
    beer_key starts with "u:" (a bar's own Untappd sighting) when one exists in the group, since a
    matched shop row's cached copy can lag it; otherwise any row in the group (today's behaviour)."""
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", _js(_soup()), re.S).group(1)
    assert re.search(r'r\.beer_key(?:\s*&&\s*r\.beer_key)?\.startsWith\(\s*["\']u:["\']\s*\)', group_fn)


def test_beers_group_by_group_key_before_beer_key():
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", _js(_soup()), re.S).group(1)
    assert group_fn.index("r.group_key") < group_fn.index("r.beer_key")


def test_back_to_top_button():
    soup = _soup()
    button = soup.find(id="to-top")
    assert button.name == "button" and button["type"] == "button" and button.has_attr("hidden")
    assert button["aria-label"] == "Наверх"
    js = _js(soup)
    assert "scrollTo" in js and 'addEventListener("scroll"' in js


def test_group_logo_is_the_first_non_empty_logo_in_the_group():
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", _js(_soup()), re.S).group(1)
    assert re.search(r"beer_logo:\s*group\.map\(\(?r\)?\s*=>\s*r\.beer_logo\)\.find\(Boolean\)", group_fn)
    assert "r0.beer_logo" not in group_fn


def test_shop_name_line_shows_only_when_it_differs_from_the_displayed_name():
    """v1.2 review aid: `в магазине: <shop_name>` under the place, for every layout that draws a place line."""
    js = _js(_soup())
    line_fn = re.search(r"function shopNameLine\(r, name\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "r.shop_name" in line_fn and "в магазине: " in line_fn
    assert re.search(r"toLowerCase\(\)", line_fn) and r"\s" in line_fn   # case/space-insensitive comparison
    for fn in ("placeCell(r, name)", "placeLines(rows, name)", "cardWhere(r, name)"):
        body = re.search(rf"function {re.escape(fn)}\s*\{{(.*?)\n\}}", js, re.S).group(1)
        assert "shopNameLine(r, name)" in body, fn
    assert re.search(r"placeLines\(g\.rows,\s*g\.name\)", js)
    assert len(re.findall(r"placeCell\(g\.rows\[0\],\s*g\.name\)", js)) == 1
    assert len(re.findall(r"cardWhere\(g\.rows\[0\],\s*g\.name\)", js)) == 1
    assert re.search(r"\.shop-name\s*\{[^}]*color:\s*var\(--muted\)", _css(_soup()))


def test_footer_links_to_the_match_review_page():
    footer = _soup().find("footer")
    link = footer.find("a", string="Проверка склеек")
    assert link["href"] == "matches.html"


def test_several_servings_read_as_container_price_and_volume_joined_by_dots():
    """v1.3: «розлив 2800 ֏ · бутылка 1500 ֏ 330 мл» -- container, price, then volume; dots between servings."""
    js = _js(_soup())
    serving_fn = re.search(r"const servingText = .*", js).group(0)
    assert "CONTAINER_RU[s.container]" in serving_fn and "priceText(s)" in serving_fn
    assert "s.volume_ml" in serving_fn and "мл" in serving_fn
    assert re.search(r'const servingsText = \(r\) => .*\.map\(servingText\)\.join\(" · "\)', js)


def test_servings_go_in_the_place_line_of_every_layout():
    js = _js(_soup())
    for fn in ("placeCell(r, name)", "placeLines(rows, name)", "cardWhere(r, name)"):
        body = re.search(rf"function {re.escape(fn)}\s*\{{(.*?)\n\}}", js, re.S).group(1)
        assert "servingsText(r)" in body, fn


def test_a_beer_with_servings_does_not_repeat_its_first_one_next_to_the_name():
    js = _js(_soup())
    assert re.search(r"const singleServing = \(g\) => g\.rows\.length === 1 && !g\.rows\[0\]\.servings", js)
    for fn in ("table(groups)", "cards(groups)"):
        body = re.search(rf"function {re.escape(fn)}\s*\{{(.*?)\n\}}", js, re.S).group(1)
        assert "singleServing(g)" in body, fn


def test_since_shows_the_time_and_sorts_by_it():
    js = _js(_soup())
    since_fn = re.search(r"function sinceText\(s\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "hm" in since_fn                       # a timestamp shows the Yerevan clock time
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "since_at" in group_fn                 # earliest full timestamp of the group
    visible_fn = re.search(r"function visibleGroups\(\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "since_at" in visible_fn               # 'по новизне' orders by the timestamp, not the day


def test_country_reads_in_russian_for_the_countries_seen_and_as_the_shop_wrote_it_otherwise():
    js = _js(_soup())
    table = re.search(r"const COUNTRY_RU = new Map\(Object\.entries\(\{(.*?)\}\)\);", js, re.S).group(1)
    for english, russian in (("Ukraine", "Украина"), ("Czech Republic", "Чехия"), ("Germany", "Германия"),
                             ("Belgium", "Бельгия"), ("Russia", "Россия"), ("Armenia", "Армения")):
        assert re.search(rf'"?{english}"?:\s*"{russian}"', table), english
    # a Map, so a country called "constructor" cannot pick up an Object property; unknown ones stay as written
    assert re.search(r'const countryText = \(r\) => COUNTRY_RU\.get\(r\.country\) \|\| r\.country \|\| ""', js)


def test_country_follows_style_and_abv_in_a_card_and_the_brewery_in_a_table_row():
    js = _js(_soup())
    cards_fn = re.search(r"function cards\(groups\)\s*\{(.*?)\n\}", js, re.S).group(1)
    meta = re.search(r"metaNode\(\[(.*?)\]\)", cards_fn).group(1)
    assert [part.strip() for part in meta.split(",")] == [
        "g.brewery", "styleNode(g)", "abvText(g)", "ibuText(g)", "countryText(g)"]
    table_fn = re.search(r"function table\(groups\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "smallNode(dot([g.brewery, countryText(g)]))" in table_fn


def test_a_group_shows_the_first_country_any_of_its_rows_states():
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", _js(_soup()), re.S).group(1)
    assert re.search(r"country:\s*group\.map\(\(?r\)?\s*=>\s*r\.country\)\.find\(Boolean\)", group_fn)


def test_a_style_guessed_from_the_name_is_set_apart_and_says_so():
    soup = _soup()
    js, css = _js(soup), _css(soup)
    style_fn = re.search(r"function styleNode\(g\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "g.style_inferred" in style_fn and '"inferred"' in style_fn and "определено по названию" in style_fn
    assert re.search(r"\.inferred\s*\{[^}]*font-style:\s*italic", css)
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "style_inferred: r0.style_inferred" in group_fn          # travels with the style it belongs to
    table_fn = re.search(r"function table\(groups\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert 'el("td", null, styleNode(g))' in table_fn
