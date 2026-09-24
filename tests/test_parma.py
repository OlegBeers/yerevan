import re
from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, HttpResponse
from taps.model import Sighting
from taps.sources.parma import (
    ParmaCard, ParmaProduct, clean_name, fetch_parma, parse_listing, parse_product, volume_ml,
)
from tests.helpers import fixture_text

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
PLACE = Place(id="parma", name="Parma", kind="shop", sources={"parma": {}})
P1 = fixture_text("parma/list_p1.html")            # 60 cards, all in stock
P4 = fixture_text("parma/list_p4.html")            # last page, 7 cards
PRODUCT_1645 = fixture_text("parma/product_1645.html")
PRODUCT_28051 = fixture_text("parma/product_28051.html")
URL_28051 = "https://parma.am/en/product/product?slug=beer-dahook-ipa-light-330ml_28051"
URL_28637 = "https://parma.am/en/product/product?slug=beer-dargett-imperial-stout-dark-330ml_28637"
GZIP = {"Accept-Encoding": "gzip"}


def list_url(page):
    return f"https://parma.am/en/product/category?slug=beer&available=false&page={page}"


def renumber(html, prefix):
    """Prefix every product code in the page, so a copy of a real page acts as a page of new items."""
    return re.sub(r"_(\d+)(?=[\"'])", lambda m: f"_{prefix}{m.group(1)}", html)


CARDS = [str(c) for c in BeautifulSoup(P1, "html.parser").select("div.product_item")]


def page(n, prefix):
    """Listing page of the first n real list_p1 cards with prefixed codes."""
    return renumber("".join(CARDS[:n]), prefix)


def all_ids(pages):
    return {c.item_id for html in pages for c in parse_listing(html)}


class FakeHttp:
    """Serves pages by URL; an exception value is raised; an unexpected URL fails the test."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, headers))
        assert url in self.pages, f"unexpected GET {url}"
        body = self.pages[url]
        if isinstance(body, Exception):
            raise body
        return HttpResponse(200, {"content-type": "text/html; charset=UTF-8"}, body)

    def urls(self):
        return [url for url, _ in self.calls]


def listing_pages(*pages):
    return {list_url(i): html for i, html in enumerate(pages, 1)}


# pages 2 and 3 are not in the fixtures: renumbered copies of page 1 stand in for them (187 cards in total)
REAL_WALK = (P1, renumber(P1, "2"), renumber(P1, "3"), P4)


# --- parse_listing ------------------------------------------------------------

def test_parse_listing_first_page():
    cards = parse_listing(P1)
    assert len(cards) == 60
    assert len({c.item_id for c in cards}) == 60
    assert cards[0] == ParmaCard(
        "48761", 'Beer "Paulaner Original" light 330ml', 970, True,
        "https://parma.am/en/product/product?slug=beer-paulaner-original-light-330ml_48761")
    by_id = {c.item_id: c for c in cards}
    assert by_id["21593"].price_amd == 520          # discount card: old price 650 is not data-price
    assert by_id["26934"].title == 'Beer "379" cherry, dark 330ml'
    baltika = by_id["21063"]                        # percent-encoded slug ending in "-_21063"
    assert baltika.title == 'Beer "Baltika №9" light 450ml'
    assert baltika.url == "https://parma.am/en/product/product?slug=beer-baltika-%E2%84%969-450ml-_21063"
    assert all(c.in_stock for c in cards)


def test_parse_listing_last_page():
    cards = parse_listing(P4)
    assert [c.item_id for c in cards] == ["99846", "28721", "28052", "28050", "28047", "28637", "28051"]
    assert cards[-1] == ParmaCard("28051", 'Beer "Dahook Ipa" light 330ml', 790, True, URL_28051)
    assert cards[5].url == URL_28637


def test_parse_listing_code_is_after_last_underscore():
    cards = parse_listing(P4.replace("beer-paulaner-weissbier-light-500ml_99846", "beer_paulaner-weissbier_99846"))
    assert (cards[0].item_id, cards[0].url) == \
        ("99846", "https://parma.am/en/product/product?slug=beer_paulaner-weissbier_99846")


def test_parse_listing_out_of_stock_card():
    soup = BeautifulSoup(P4, "html.parser")
    last = soup.select("div.product_item")[-1]
    last.append(soup.new_tag("div", attrs={"class": "not_av_content"}))
    cards = parse_listing(str(soup))
    assert [c.in_stock for c in cards] == [True] * 6 + [False]
    assert cards[-1].item_id == "28051" and cards[-1].price_amd == 790


def _drop_title_span(html):
    soup = BeautifulSoup(html, "html.parser")
    soup.select_one("a.item_name > span").decompose()
    return str(soup)


@pytest.mark.parametrize("mutate", [
    lambda h: h.replace("beer-paulaner-weissbier-light-500ml_99846", "beer-paulaner-weissbier-light-500ml"),
    lambda h: h.replace("_99846", "_abc"),
    lambda h: h.replace('href="/en/product/product?slug=beer-paulaner-weissbier',
                        'href="https://evil.example/en/product/product?slug=beer-paulaner-weissbier'),
    _drop_title_span,
], ids=["no-code", "code-not-digits", "other-host", "no-title"])
def test_parse_listing_skips_card_without_code_title_or_parma_link(mutate):
    cards = parse_listing(mutate(P4))
    assert [c.item_id for c in cards] == ["28721", "28052", "28050", "28047", "28637", "28051"]


@pytest.mark.parametrize("bad", ["", "1 270", "abc"])
def test_parse_listing_keeps_card_with_unreadable_price(bad):
    cards = parse_listing(P4.replace('data-price="1270"', f'data-price="{bad}"'))
    assert (cards[0].item_id, cards[0].price_amd) == ("99846", None)
    assert len(cards) == 7


# --- parse_product ------------------------------------------------------------

def test_parse_product_fixtures():
    assert parse_product(PRODUCT_1645) == ParmaProduct("ЗАО МПК", "Russia", 5.9)
    assert parse_product(PRODUCT_28051) == ParmaProduct("Dahook LLC", "Armenia", 6.0)


@pytest.mark.parametrize("written", ["5,9%", "5\u20249%", "5.9 %"])
def test_parse_product_abv_separators(written):
    assert parse_product(PRODUCT_1645.replace("5.9%", written)).abv == 5.9


def test_parse_product_reads_abv_only_from_description():
    assert "-19%" in PRODUCT_1645                   # discount badges of related products
    assert parse_product(PRODUCT_1645.replace("Alcohol volume: 5.9%.", "")).abv is None


def test_parse_product_missing_fields():
    assert parse_product("<html><body><p>Not found</p></body></html>") == ParmaProduct(None, None, None)


# --- name and volume ----------------------------------------------------------

@pytest.mark.parametrize("title, name, ml", [
    ('Beer "Dahook Ipa" light 330ml', "Dahook Ipa light", 330),
    ('Beer "379" cherry, dark 330ml', "379 cherry, dark", 330),
    ('Beer "Baltika №9" light 450ml', "Baltika №9 light", 450),
    ('Beer "Volfas Engelman Sviesusis 1410" light 568ml', "Volfas Engelman Sviesusis 1410 light", 568),
    ('Beer "Trappistes Rochefort 10" dark 330ml', "Trappistes Rochefort 10 dark", 330),
    ('Beer "Kilikia" 1.5l', "Kilikia", 1500),
    ('Beer "Kilikia"', "Kilikia", None),
])
def test_clean_name_and_volume(title, name, ml):
    assert clean_name(title) == name
    assert volume_ml(title) == ml


# --- fetch_parma --------------------------------------------------------------

def test_fetch_parma_walks_pages_and_fetches_product_pages_only_for_new_codes():
    known = all_ids(REAL_WALK) - {"28051", "28637"}
    http = FakeHttp({**listing_pages(*REAL_WALK),
                     URL_28051: PRODUCT_28051,
                     URL_28637: FetchError("http", "404")})
    result = fetch_parma(http, PLACE, known, NOW, {})

    assert (result.ok, result.error, result.key, result.source, result.place_id, result.full) == \
        (True, None, "parma:parma", "parma", "parma", True)
    # page 4 has 7 cards, so page 5 is never asked for; then only the two new codes get product pages
    assert http.urls() == [list_url(1), list_url(2), list_url(3), list_url(4), URL_28637, URL_28051]
    assert all(headers == GZIP for _, headers in http.calls)

    by_id = {s.shop_item_id: s for s in result.sightings}
    assert len(result.sightings) == len(by_id) == 186      # 187 cards minus the one whose product page failed
    assert "28637" not in by_id
    assert by_id["28051"] == Sighting(
        place_id="parma", source="parma", beer_key="n:dahook ipa light", title='Beer "Dahook Ipa" light 330ml',
        name="Dahook Ipa light", seen_at=NOW, brewery="Dahook LLC", shop_item_id="28051", abv=6.0,
        price_amd=790, volume_ml=330, in_stock=True, category="beer", url=URL_28051, shop_url=URL_28051)
    vimpel = by_id["21593"]                                 # known code: no product page, so no brewery or abv
    assert (vimpel.beer_key, vimpel.brewery, vimpel.abv, vimpel.price_amd, vimpel.in_stock) == \
        ("n:vimpel lager light", None, None, 520, True)
    assert by_id["26934"].beer_key == "n:379 cherry dark"
    assert by_id["226934"].title == 'Beer "379" cherry, dark 330ml'   # renumbered copy on page 2


def test_fetch_parma_sighting_keeps_out_of_stock_flag():
    soup = BeautifulSoup(P4, "html.parser")
    soup.select("div.product_item")[-1].append(soup.new_tag("div", attrs={"class": "not_av_content"}))
    pages = (P1, renumber(P1, "2"), renumber(P1, "3"), str(soup))
    http = FakeHttp({**listing_pages(*pages), URL_28051: PRODUCT_28051})
    result = fetch_parma(http, PLACE, all_ids(pages) - {"28051"}, NOW, {})
    by_id = {s.shop_item_id: s for s in result.sightings}
    assert (by_id["28051"].in_stock, by_id["28051"].brewery) == (False, "Dahook LLC")
    assert by_id["28637"].in_stock is True


def test_fetch_parma_uses_brewery_aliases_in_keys():
    http = FakeHttp(listing_pages(*REAL_WALK))
    result = fetch_parma(http, PLACE, all_ids(REAL_WALK), NOW, {"dahook": "dahook craft"})
    by_id = {s.shop_item_id: s for s in result.sightings}
    assert by_id["28051"].beer_key == "n:dahook craft ipa light"


def test_fetch_parma_skips_title_without_key_and_its_product_page():
    pages = (P1, renumber(P1, "2"), renumber(P1, "3"),
             P4.replace('Beer "Paulaner Weissbier" light 500ml', "Beer 500ml"))
    http = FakeHttp(listing_pages(*pages))                  # no product page for 99846 is served
    result = fetch_parma(http, PLACE, all_ids(pages) - {"99846"}, NOW, {})
    assert result.ok
    assert "99846" not in {s.shop_item_id for s in result.sightings}
    assert len(result.sightings) == 186


def test_fetch_parma_stops_after_eight_pages():
    pages = [page(60, str(i)) for i in range(1, 10)]        # a 9th full page exists but must not be read
    http = FakeHttp(listing_pages(*pages))
    result = fetch_parma(http, PLACE, all_ids(pages), NOW, {})
    assert http.urls() == [list_url(i) for i in range(1, 9)]
    assert result.ok and len(result.sightings) == 480


def test_fetch_parma_counts_each_code_once():
    http = FakeHttp({list_url(i): P1 for i in range(1, 9)})   # site ignores ?page=: same 60 cards every time
    result = fetch_parma(http, PLACE, set(), NOW, {})
    assert len(http.calls) == 8
    assert (result.ok, result.error, result.sightings) == (False, "empty", [])


@pytest.mark.parametrize("last, ok", [(29, False), (30, True)])
def test_fetch_parma_needs_150_cards(last, ok):
    pages = (page(60, "1"), page(60, "2"), page(last, "3"))
    http = FakeHttp(listing_pages(*pages))
    result = fetch_parma(http, PLACE, all_ids(pages), NOW, {})
    assert http.urls() == [list_url(1), list_url(2), list_url(3)]   # stops after the short page
    assert result.ok is ok
    assert result.error == (None if ok else "empty")
    assert len(result.sightings) == (150 if ok else 0)


def test_fetch_parma_short_first_page_is_empty_without_product_requests():
    http = FakeHttp(listing_pages(P4))
    result = fetch_parma(http, PLACE, set(), NOW, {})
    assert http.urls() == [list_url(1)]
    assert (result.ok, result.error, result.key, result.place_id) == (False, "empty", "parma:parma", "parma")


@pytest.mark.parametrize("kind", ["network", "cloudflare", "http"])
def test_fetch_parma_listing_failure_fails_the_run(kind):
    http = FakeHttp({list_url(1): P1, list_url(2): FetchError(kind, list_url(2))})
    result = fetch_parma(http, PLACE, set(), NOW, {})
    assert http.urls() == [list_url(1), list_url(2)]
    assert (result.ok, result.error, result.sightings, result.key) == (False, kind, [], "parma:parma")
