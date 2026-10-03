import pytest

from taps.fetch import FetchError, HttpResponse
from taps.sources.robots import Robots, read_robots
from tests.helpers import fixture_text

SAS = Robots.parse(fixture_text("sas/robots.txt"))
CARREFOUR = Robots.parse(fixture_text("carrefour/robots.txt"))


def test_rules_come_only_from_the_star_group():
    assert "/bitrix/" in SAS.disallow and "/*PAGEN" in SAS.disallow
    assert "/" not in SAS.disallow   # "User-Agent: SemrushBot / Disallow: /" is another group's rule
    assert "/catalog/" in CARREFOUR.disallow and "/*?" in CARREFOUR.disallow


@pytest.mark.parametrize("url", [
    "https://www.sas.am/en/catalog/armyanskoe/",
    "https://www.sas.am/en/catalog/armyanskoe/?offset=24",
    "https://www.sas.am/en/catalog/armyanskoe/135534/",
    "https://www.sas.am/robots.txt",
])
def test_sas_allows_the_catalog_and_its_offset_pagination(url):
    assert SAS.allowed(url)


@pytest.mark.parametrize("url", [
    "https://www.sas.am/en/catalog/armyanskoe/?PAGEN_1=2",
    "https://www.sas.am/en/catalog/armyanskoe/?sort=price",
    "https://www.sas.am/en/catalog/armyanskoe/?order=asc",
    "https://www.sas.am/ajax/catalog.php",
    "https://www.sas.am/en/search/?q=beer",
    "https://www.sas.am/bitrix/admin/",
    "https://www.sas.am/en/personal/order/",
])
def test_sas_blocks_what_robots_forbids(url):
    assert not SAS.allowed(url)


def test_carrefour_allows_plain_pages_and_blocks_any_query_string():
    assert CARREFOUR.allowed("https://carrefour.am/en/everyday-products/alcoholic-beverages/beer")
    assert CARREFOUR.allowed("https://carrefour.am/sitemap_en.xml")
    assert not CARREFOUR.allowed("https://carrefour.am/en/everyday-products/alcoholic-beverages/beer?p=2")
    assert not CARREFOUR.allowed("https://carrefour.am/catalog/product/view/id/1")
    assert not CARREFOUR.allowed("https://carrefour.am/en/page.php")      # "/*.php$" anchors the end
    assert CARREFOUR.allowed("https://carrefour.am/en/php-beer")


def test_a_group_with_an_empty_disallow_allows_everything():
    assert Robots.parse("User-agent: *\nDisallow:\n").allowed("https://x.am/anything?q=1")


def test_text_without_a_star_group_allows_everything():
    assert Robots.parse("User-agent: Bot\nDisallow: /\n").allowed("https://x.am/")


def test_read_robots_fetches_the_sites_robots_txt():
    class Http:
        def get(self, url, headers=None):
            assert url == "https://www.sas.am/robots.txt"
            return HttpResponse(200, {}, fixture_text("sas/robots.txt"))

    assert not read_robots(Http(), "https://www.sas.am").allowed("https://www.sas.am/ajax/x")


def test_read_robots_failure_is_a_fetch_error():
    class Http:
        def get(self, url, headers=None):
            raise FetchError("http", "503")

    with pytest.raises(FetchError):
        read_robots(Http(), "https://www.sas.am")
