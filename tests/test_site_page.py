import re
from pathlib import Path

from bs4 import BeautifulSoup

PAGE = Path(__file__).resolve().parent.parent / "site" / "index.html"

ROW_FIELDS = ("place_id", "section", "name", "brewery", "style", "abv", "ibu", "rating", "price_amd",
              "volume_ml", "container", "badge", "since", "seen_days_ago", "new", "star", "url", "by", "serving",
              "beer_logo", "shop_url", "beer_key", "shop_name", "servings", "country", "style_inferred")
PLACE_FIELDS = ("id", "name", "section", "last_ok", "menu_updated_at", "failing", "failing_days",
                "logo", "verified", "untappd_url", "address", "map_url")
VENUE_FIELDS = ("name", "url", "logo", "verified", "checkins_30d", "last_checkin", "tracked", "address", "map_url")


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


def test_places_row_fades_out_at_its_right_edge_on_narrow_screens():
    css = _css(_soup())
    block = css[css.index("max-width: 699px"):]
    places = re.search(r"\.places\s*\{([^}]*)\}", block).group(1)
    assert "mask-image: linear-gradient(to right" in places and "-webkit-mask-image" in places
    assert "transparent" in places


def test_spacing_is_a_scale_of_four_custom_properties():
    css = _css(_soup())
    root = re.search(r":root\s*\{([^}]*)\}", css).group(1)
    for token, size in (("--s1", 4), ("--s2", 8), ("--s3", 12), ("--s4", 16)):
        assert re.search(rf"{token}:\s*{size}px", root), token
    assert re.search(r"--hit:\s*44px", root)                     # the smallest touch target
    assert re.search(r"\.wrap\s*\{[^}]*padding:[^;}]*var\(--s4\)", css)


def test_filters_and_badges_are_chips():
    """One pill component for the place filters, the venues tag and (below) badges and servings."""
    soup = _soup()
    js, css = _js(soup), _css(soup)
    chip = re.search(r"\.chip\s*\{([^}]*)\}", css).group(1)
    assert "border-radius: 999px" in chip and "font-variant-numeric: tabular-nums" in chip
    assert 'class: "place chip"' in js and 'class: "chip tracked-badge"' in js
    assert re.search(r"\.place\s*\{[^}]*min-height:\s*var\(--hit\)", css)


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


def _contrast(foreground, background):
    """WCAG contrast ratio of two #rrggbb colours."""
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_muted_text_and_the_hot_rating_keep_aa_contrast_on_every_surface_in_both_themes():
    """Chips and lines of muted text sit on the page, the card and the chip surface; the hot chip on the card."""
    css = _css(_soup())
    tokens = lambda block: dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", block))
    light = tokens(re.search(r":root\s*\{([^}]*)\}", css).group(1))
    dark = {**light, **tokens(re.search(r"prefers-color-scheme: dark\)\s*\{\s*:root\s*\{([^}]*)\}", css).group(1))}
    assert dark["--bg"] != light["--bg"]
    for theme in (light, dark):
        for surface in ("--bg", "--surface", "--surface-2"):
            assert _contrast(theme["--muted"], theme[surface]) >= 4.5, (theme["--bg"], surface)
        assert _contrast(theme["--hot"], theme["--surface"]) >= 4.5, theme["--bg"]
        assert _contrast(theme["--danger"], theme["--surface-2"]) >= 4.5, theme["--bg"]


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


def test_search_ignores_a_style_that_was_only_inferred_from_the_name():
    js = _js(_soup())
    filter_fn = re.search(r"function filteredRows\(\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "r.style_inferred ? null : r.style" in filter_fn


def test_group_logo_prefers_untappd_rows_then_matched_rows_then_any():
    js = _js(_soup())
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", js, re.S).group(1)
    logo = re.search(r"beer_logo: (.*?\.find\(Boolean\)),\n", group_fn, re.S).group(1)
    assert logo.index('startsWith("u:")') < logo.index("match_via") < logo.index("r.beer_logo")
    assert "r0.beer_logo" not in group_fn


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


def test_every_beer_lists_a_block_per_place_with_its_own_source_price_and_address():
    js = _js(_soup())
    assert "function placeBlock(r, name, withServings = true)" in js
    block_fn = re.search(r"function placeBlock\(r, name, withServings = true\)\s*\{(.*?)\n\}", js, re.S).group(1)
    for part in ("sourceBadge(r)", "servingChips(r)", "seenNote(r)", "mapLinkNode(place)", "shopNameLine(r, name)"):
        assert part in block_fn, part
    assert block_fn.index("sourceBadge(r)") < block_fn.index("servingChips(r)") < block_fn.index("mapLinkNode(place)") \
        < block_fn.index("shopNameLine(r, name)")                    # the four lines of a block, in this order
    assert re.search(r"g\.rows\.map\(\(r\) => placeBlock\(r, g\.name\)\)", js)


def test_single_and_multi_place_beers_share_one_anatomy():
    """No branch on the number of places: a beer at one place is a beer at several places with one block."""
    js = _js(_soup())
    card_fn = re.search(r"function cardNode\(g\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "g.rows.length" not in card_fn and "singleServing" not in js
    for old in ("placeCell", "placeLines", "cardWhere", "badgeNodes", "shopLinkNode", "beerLogoNode", "servingsText"):
        assert old not in js, old
    parts = [card_fn.index(part) for part in ('"card-head"', "metaNode(", '"place-blocks"', '"card-foot"')]
    assert parts == sorted(parts)                                    # header, meta, places, footer


def test_place_blocks_are_stacked_and_each_has_a_thin_left_border():
    css = _css(_soup())
    assert re.search(r"\.place-blocks\s*\{[^}]*display:\s*grid", css)
    block = re.search(r"\.place-block\s*\{([^}]*)\}", css).group(1)
    assert re.search(r"border-left:\s*2px solid var\(--border\)", block) and "padding-left: var(--s3)" in block
    assert re.search(r"\.chips\s*\{[^}]*flex-wrap:\s*wrap", css)


def test_the_card_header_is_a_grid_of_photo_name_and_a_rating_slot():
    css = _css(_soup())
    root = re.search(r":root\s*\{([^}]*)\}", css).group(1)
    assert re.search(r"--thumb:\s*(4[4-8])px", root)                  # a photo of a fixed size, 44 to 48px
    head = re.search(r"\.card-head\s*\{([^}]*)\}", css).group(1)
    assert "display: grid" in head and "grid-template-columns: auto 1fr auto" in head
    assert re.search(r"\.thumb\s*\{[^}]*width:\s*var\(--thumb\)[^}]*height:\s*var\(--thumb\)", css)
    assert re.search(r"\.card-name\s*\{[^}]*min-width:\s*0", css)     # a long name wraps in its column, it pushes nothing
    slot = re.search(r"\.rate-slot\s*\{([^}]*)\}", css).group(1)
    assert "display: flex" in slot and "margin-left: var(--s3)" in slot   # the gap belongs to the slot: none when it is left out


def test_the_rating_is_a_chip_of_one_size_with_the_fire_inside_it_when_hot():
    soup = _soup()
    js, css = _js(soup), _css(soup)
    rating_fn = re.search(r"function ratingNode\(r\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "chip chip-rating hot" in rating_fn and "chip chip-rating" in rating_fn
    assert 'iconSpan(hot ? "🔥" : "★")' in rating_fn and "toFixed(2)" in rating_fn
    assert re.search(r"\.chip-rating\s*\{[^}]*min-width:[^}]*justify-content:\s*center", css)   # hot and plain chips alike
    assert re.search(r"\.hot\s*\{[^}]*color:\s*var\(--hot\)", css)
    slot_fn = re.search(r"function cardNode\(g\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert re.search(r'flags\.length \|\| rating \? el\("div", \{ class: "rate-slot" \}, \.\.\.flags, rating\) : null', slot_fn)


def test_the_new_and_first_time_flags_are_small_chips_that_travel_with_the_rating():
    js = _js(_soup())
    flags_fn = re.search(r"function flagNodes\(g\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert 'class: "chip chip-flag"' in flags_fn and 'role: "img"' in flags_fn and "aria-label" in flags_fn
    assert flags_fn.index("g.star") < flags_fn.index("g.new")


def test_the_photo_is_always_there_a_tile_takes_its_place_and_a_broken_picture_becomes_the_tile():
    js = _js(_soup())
    thumb_fn = re.search(r"function thumbNode\(url\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert 'class: "thumb thumb-empty"' in thumb_fn and 'class: "thumb"' in thumb_fn
    assert "safeUrl(url)" in thumb_fn and 'referrerpolicy: "no-referrer"' in thumb_fn
    assert re.search(r'addEventListener\("error", \(\) => img\.replaceWith\(tile\(\)\)', thumb_fn)


def test_a_place_block_names_its_source_and_a_checkin_says_when_it_was_seen_in_a_muted_pill():
    soup = _soup()
    js, css = _js(soup), _css(soup)
    source_fn = re.search(r"function sourceBadge\(r\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert '"chip chip-source"' in source_fn and "safeUrl(r.shop_url)" in source_fn and "shop !== r.url" in source_fn
    assert 'target: "_blank"' in source_fn and 'rel: "noopener noreferrer"' in source_fn
    label_fn = re.search(r"function sourceLabel\(r\)\s*\{(.*?)\n\}", js, re.S).group(1)
    for key in ('"badge.menu"', '"badge.checkin"', '"badge.manual.by"', '"badge.manual"', '"shop.in"'):
        assert key in label_fn, key
    seen_fn = re.search(r"function seenNote\(r\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert '"chip chip-note"' in seen_fn and 'iconSpan("👀")' in seen_fn      # the eyes live inside the pill
    assert 't("badge.seen.ago", { ago: ago(r.seen_days_ago) })' in seen_fn and "servingLabel(r.serving)" in seen_fn
    assert re.search(r"\.chip-source,\s*\.chip-note\s*\{[^}]*color:\s*var\(--muted\)", css)     # both read as muted text


def test_the_card_ends_with_when_the_beer_appeared_in_muted_small_text():
    soup = _soup()
    js, css = _js(soup), _css(soup)
    assert 'el("div", { class: "card-foot" }, t("appeared", { date: since }))' in js
    assert re.search(r"\.card-foot\s*\{[^}]*color:\s*var\(--muted\)[^}]*font-size:\s*\.78rem", css)


def test_every_link_in_a_card_gets_a_44px_hit_area_without_growing():
    css = _css(_soup())
    assert re.search(r"\.card a\s*\{[^}]*position:\s*relative", css)
    hit = re.search(r"\.card a::after\s*\{([^}]*)\}", css).group(1)
    assert "position: absolute" in hit and "height: max(var(--hit), 100%)" in hit


def test_card_and_chip_spacing_comes_from_the_scale():
    css = _css(_soup())
    for selector in (r"\.card", r"\.card-head", r"\.card-name", r"\.rate-slot", r"\.place-blocks", r"\.place-block",
                     r"\.pb-head", r"\.chips", r"\.chip"):
        rule = re.search(rf"(?<![\w-]){selector}\s*\{{([^}}]*)\}}", css).group(1)
        for prop, value in re.findall(r"\b((?:padding|margin|gap)[\w-]*):\s*([^;]+);", rule):
            for length in re.findall(r"-?\d+(?:\.\d+)?px", value):
                assert False, f"{selector} {prop}: {value} is not on the scale"


def test_table_numbers_line_up_on_the_right_in_tabular_figures():
    soup = _soup()
    js, css = _js(soup), _css(soup)
    num = re.search(r"\.num\s*\{([^}]*)\}", css).group(1)
    assert "text-align: right" in num and "font-variant-numeric: tabular-nums" in num
    table_fn = re.search(r"function table\(groups\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert re.search(r'columnClass = \{ "th\.abv": "num", "th\.ibu": "num", "th\.rating": "num", "th\.price": "num"', table_fn)   # the headings too
    assert re.search(r"td\.num \.chips\s*\{[^}]*flex-direction:\s*column[^}]*align-items:\s*flex-end", css)   # several servings, stacked on the right


def test_a_table_row_reads_like_a_card_the_same_chips_in_the_same_style():
    js = _js(_soup())
    table_fn = re.search(r"function table\(groups\)\s*\{(.*?)\n\}", js, re.S).group(1)
    for part in ("thumbNode(g.beer_logo)", "ratingNode(g)", "servingChips(g.rows[0])", "placeBlock(r, g.name", "flagNodes(g)"):
        assert part in table_fn, part
    assert 'class: "beer-cell"' in table_fn                            # the photo beside the name and, under the name, the brewery
    assert 'el("div", { class: "since" }, ...flagNodes(g), sinceText(g.since_at))' in table_fn   # the flags go with the date


def test_the_beer_and_where_columns_have_room_for_a_name_and_for_a_place_block():
    soup = _soup()
    js, css = _js(soup), _css(soup)
    table_fn = re.search(r"function table\(groups\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert '"th.beer": "col-beer"' in table_fn and '"th.where": "col-where"' in table_fn
    assert re.search(r"\.col-beer\s*\{[^}]*min-width:\s*\d+rem", css) and re.search(r"\.col-where\s*\{[^}]*min-width:\s*\d+rem", css)


def test_a_venue_card_has_the_header_of_a_beer_card_a_logo_the_name_and_the_tag_in_the_slot():
    soup = _soup()
    js, css = _js(soup), _css(soup)
    venue_fn = re.search(r"function venueNode\(v\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert 'class: "card-head"' in venue_fn and "logoNode(v.name, v.logo, 44)" in venue_fn
    assert 'class: "card-name"' in venue_fn and "placeNameNode(place)" in venue_fn
    assert re.search(r'v\.tracked \? el\("div", \{ class: "rate-slot" \}, el\("span", \{ class: "chip tracked-badge" \}', venue_fn)
    assert 'class: "top"' not in js and "where-head" not in js and "where-head" not in css     # the old header is gone
    assert re.search(r"\.card-head \.avatar\s*\{[^}]*font-size", css)                           # a letter fits its 44px circle


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


def test_shop_name_line_shows_only_when_it_differs_from_the_displayed_name():
    """v1.2 review aid: `в магазине: <shop_name>` under the place, in every layout that draws a place."""
    js = _js(_soup())
    line_fn = re.search(r"function shopNameLine\(r, name\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "r.shop_name" in line_fn and 't("shop.name", { name: r.shop_name })' in line_fn   # «в магазине: {name}», see test_site_i18n
    assert re.search(r"toLowerCase\(\)", line_fn) and r"\s" in line_fn   # case/space-insensitive comparison
    body = re.search(r"function placeBlock\(r, name, withServings = true\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "shopNameLine(r, name)" in body
    assert len(re.findall(r"placeBlock\(r, g\.name", js)) == 2       # the cards and the table's place column
    assert re.search(r"\.shop-name\s*\{[^}]*color:\s*var\(--muted\)", _css(_soup()))


def test_footer_links_to_the_match_review_page():
    footer = _soup().find("footer")
    link = footer.find("a", string="Проверка склеек")
    assert link["href"] == "matches.html"


def test_a_serving_reads_as_container_and_volume_then_a_dot_and_the_price():
    """v1.4: «розлив · 2900 ֏», «банка 450 мл · 2900 ֏» -- one pill per serving, the same for a row and for its `servings`."""
    js = _js(_soup())
    volume_fn = re.search(r"const volumeText = .*", js).group(0)
    assert "s.volume_ml" in volume_fn and 't("unit.ml")' in volume_fn
    serving_fn = re.search(r"const servingText = .*", js).group(0)
    assert re.search(r"dot\(\[\[containerText\(s\.container\), volumeText\(s\)\]\.filter\(Boolean\)\.join\(\" \"\), priceText\(s\)\]\)", serving_fn)
    chips_fn = re.search(r"const servingChips = .*?;\n", js, re.S).group(0)
    assert "(r.servings || [r])" in chips_fn and "servingText" in chips_fn and '"chip chip-serving"' in chips_fn


def test_servings_are_pills_in_their_place_block_and_nowhere_else_on_a_card():
    js = _js(_soup())
    body = re.search(r"function placeBlock\(r, name, withServings = true\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "withServings ? servingChips(r) : []" in body
    card_fn = re.search(r"function cardNode\(g\)\s*\{(.*?)\n\}", js, re.S).group(1)
    for part in ("servingChips", "servingText", "priceText", "volumeText"):
        assert part not in card_fn, part                             # never next to the beer's own meta line


def test_a_table_row_of_one_place_shows_its_servings_in_the_price_column_and_several_places_in_their_blocks():
    js = _js(_soup())
    table_fn = re.search(r"function table\(groups\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert re.search(r'el\("td", \{ class: "num" \}, g\.rows\.length === 1 \? chipsNode\(servingChips\(g\.rows\[0\]\)\) : null\)', table_fn)
    assert "placeBlock(r, g.name, g.rows.length > 1)" in table_fn


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
    # a Map, so a country called "constructor" cannot pick up an Object property; unknown ones stay as written,
    # and in English every country stays as the shop wrote it
    assert re.search(r'const countryText = \(r\) => \(ui\.lang === "ru" \? COUNTRY_RU\.get\(r\.country\) : null\) '
                     r'\|\| r\.country \|\| ""', js)


def test_country_follows_style_and_abv_in_a_card_and_the_brewery_in_a_table_row():
    js = _js(_soup())
    cards_fn = re.search(r"function cardNode\(g\)\s*\{(.*?)\n\}", js, re.S).group(1)
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
    assert "g.style_inferred" in style_fn and '"inferred"' in style_fn and 'title: t("style.inferred")' in style_fn
    assert re.search(r"\.inferred\s*\{[^}]*font-style:\s*italic", css)
    group_fn = re.search(r"function groupBeers\(rows\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "style_inferred: r0.style_inferred" in group_fn          # travels with the style it belongs to
    table_fn = re.search(r"function table\(groups\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert 'el("td", null, styleNode(g))' in table_fn


def test_the_map_link_is_shown_in_every_place_block_and_the_venues_tab():
    """The pin + address link: the third line of a place block (cards and table alike) and the venues tab."""
    js = _js(_soup())
    for fn in ("placeBlock(r, name, withServings = true)", "venueNode(v)"):
        body = re.search(rf"function {re.escape(fn)}\s*\{{(.*?)\n\}}", js, re.S).group(1)
        assert "mapLinkNode(place)" in body, fn


def test_the_map_link_is_a_safe_pin_plus_address_link_that_opens_yandex_maps_in_a_new_tab():
    js = _js(_soup())
    fn = re.search(r"function mapLinkNode\(place\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "safeUrl(place.map_url)" in fn                       # the same URL check as every other link
    assert "https:" in fn                                       # https only: an http link is dropped
    assert 'target: "_blank"' in fn and 'rel: "noopener noreferrer"' in fn
    assert 't("map.open", { where: place.address || place.name })' in fn     # «Открыть на Яндекс Картах: …», see test_site_i18n
    assert "pinIcon()" in fn and 'place.address || t("map.link")' in fn     # the address is the link text; chains keep a button
    assert "null" in fn                                         # no map_url: no link


def test_the_pin_is_an_inline_svg_in_the_current_colour_built_without_html_strings():
    js = _js(_soup())
    fn = re.search(r"function pinIcon\(\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "createElementNS" in fn and 'stroke: "currentColor"' in fn
    assert '"aria-hidden": "true"' in fn
    assert "innerHTML" not in js


def test_the_map_link_is_muted_text_with_an_accent_pin_taking_colours_from_the_theme_tokens():
    css = _css(_soup())
    rule = re.search(r"\.map-link\s*\{([^}]*)\}", css).group(1)
    assert "var(--muted)" in rule
    assert "var(--accent)" in re.search(r"\.map-link svg\s*\{([^}]*)\}", css).group(1)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb", rule)       # colours only via the tokens: dark theme follows
    assert "🗺" not in _js(_soup())                              # the old emoji button is gone


def test_the_address_is_the_place_name_tooltip_and_the_venues_tab_links_it():
    js = _js(_soup())
    name_fn = re.search(r"function placeNameNode\(place\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert name_fn.count("title: place.address") == 2           # the link and the plain-text variants alike
    venue_fn = re.search(r"function venueNode\(v\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "mapLinkNode(place)" in venue_fn                     # the visible address is the map link's text
    assert "address: v.address" in venue_fn and "map_url: v.map_url" in venue_fn    # the venue is a place-shaped object


def test_the_place_chip_tooltip_leads_with_the_address():
    js = _js(_soup())
    status_fn = re.search(r"function placeStatus\(p, now\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert re.search(r"if \(p\.address\) lines\.push\(\{ text: p\.address, warn: false \}\)", status_fn)
    assert "addresses" not in js                                # data.json no longer carries the list
