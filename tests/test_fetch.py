from datetime import datetime, timedelta, timezone

import pytest
import requests

from taps.fetch import (UA, FetchError, Http, HttpResponse, UntappdClient,
                        is_cloudflare_challenge, untappd_due)
from taps.state import UntappdRec
from taps.timeutil import iso
from tests.helpers import fixture_text

NOW = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)   # 18:00 in Yerevan, date 2026-09-23


def challenge_headers() -> dict[str, str]:
    lines = fixture_text("untappd/cloudflare_challenge.headers").splitlines()[1:]   # skip "HTTP/2 403"
    return {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines if ":" in l)}


CF_HTML = fixture_text("untappd/cloudflare_challenge.html")
CF = HttpResponse(403, challenge_headers(), CF_HTML)
OK = HttpResponse(200, {"content-type": "text/html"}, "<html>menu</html>")


# --- Cloudflare detection -------------------------------------------------

def test_challenge_fixture_is_detected():
    assert CF.headers["cf-mitigated"] == "challenge"
    assert is_cloudflare_challenge(403, CF.headers, CF_HTML)


def test_each_challenge_signal_alone_is_enough():
    assert is_cloudflare_challenge(200, {"cf-mitigated": "challenge"}, "")
    assert is_cloudflare_challenge(200, {}, CF_HTML)   # body markers only, any page language
    assert is_cloudflare_challenge(200, {}, "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1'>")
    assert is_cloudflare_challenge(200, {}, "<TITLE>JUST A MOMENT...</title>")
    assert is_cloudflare_challenge(403, {"server": "Cloudflare"}, "")
    assert is_cloudflare_challenge(403, {"server": "nginx"}, "forbidden")   # any 403 is treated as a challenge
    assert is_cloudflare_challenge(200, {"cf-mitigated": "static"}, "")     # any presence of the header counts
    assert not is_cloudflare_challenge(200, {}, "plain 200 page")


def test_normal_page_with_the_cloudflare_bot_management_script_is_not_a_challenge():
    page = "<html><script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script><body>menu</body></html>"
    assert not is_cloudflare_challenge(200, {}, page)


def test_real_menu_page_is_not_a_challenge():
    html = fixture_text("untappd/gargoyle_menu.html")
    assert "Gargoyle Bar" in html
    assert not is_cloudflare_challenge(200, {"server": "cloudflare"}, html)


# --- Http -------------------------------------------------------------------

class FakeRaw:
    def __init__(self, status=200, text="ok", headers=None, json_data=None):
        self.status_code, self.text = status, text
        self.headers = headers or {"Content-Type": "text/html"}
        self._json = json_data

    def json(self):
        if self._json is None:
            raise requests.JSONDecodeError("Expecting value", self.text, 0)
        return self._json


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, **kw):
        return self._next("get", url, **kw)

    def post(self, url, **kw):
        return self._next("post", url, **kw)


def make_http(*responses):
    sleeps = []
    session = FakeSession(*responses)
    return Http(session=session, sleep=sleeps.append), session, sleeps


def test_http_get_sends_ua_and_delays_only_between_requests():
    http, session, sleeps = make_http(FakeRaw(text="a", headers={"X-Thing": "1"}), FakeRaw(text="b"))
    first = http.get("https://shop.example/1", headers={"Accept": "application/json"})
    assert sleeps == []
    assert first == HttpResponse(200, {"x-thing": "1"}, "a")
    assert http.get("https://shop.example/2").text == "b"
    assert len(sleeps) == 1 and 1.5 <= sleeps[0] <= 2.0
    _, url, kw = session.calls[0]
    assert url == "https://shop.example/1"
    assert kw["headers"] == {"User-Agent": UA, "Accept": "application/json"}
    assert kw["timeout"] > 0
    assert session.calls[1][2]["headers"]["User-Agent"] == UA


def test_http_request_exception_is_network_error():
    http, _, _ = make_http(requests.ConnectionError("dns failed"))
    with pytest.raises(FetchError) as e:
        http.get("https://shop.example/")
    assert e.value.kind == "network"


def test_http_challenge_is_cloudflare_error():
    http, _, _ = make_http(FakeRaw(403, CF_HTML, {"Cf-Mitigated": "challenge", "Server": "cloudflare"}))
    with pytest.raises(FetchError) as e:
        http.get("https://shop.example/")
    assert e.value.kind == "cloudflare"


def test_http_404_is_http_error():
    http, _, _ = make_http(FakeRaw(404, "not found"))
    with pytest.raises(FetchError) as e:
        http.get("https://shop.example/missing")
    assert e.value.kind == "http"


def test_post_json_sends_payload_and_returns_parsed_json():
    http, session, sleeps = make_http(FakeRaw(json_data={"items": [1, 2]}))
    assert http.post_json("https://shop.example/api", {"page": 1}) == {"items": [1, 2]}
    method, _, kw = session.calls[0]
    assert method == "post" and kw["json"] == {"page": 1}
    assert kw["headers"]["User-Agent"] == UA
    assert sleeps == []


def test_post_json_invalid_json_is_fetch_error():
    http, _, _ = make_http(FakeRaw(text="<html>maintenance</html>"))
    with pytest.raises(FetchError) as e:
        http.post_json("https://shop.example/api", {})
    assert e.value.kind == "http"


def test_http_delay_also_follows_a_failed_request():
    http, _, sleeps = make_http(requests.Timeout("slow"), FakeRaw())
    with pytest.raises(FetchError):
        http.get("https://shop.example/1")
    http.get("https://shop.example/2")
    assert len(sleeps) == 1


# --- untappd_due --------------------------------------------------------------

def test_untappd_due():
    assert untappd_due(UntappdRec(), NOW)
    assert not untappd_due(UntappdRec(last_attempt=iso(NOW - timedelta(hours=19))), NOW)
    assert untappd_due(UntappdRec(last_attempt=iso(NOW - timedelta(hours=20))), NOW)


# --- UntappdClient ------------------------------------------------------------

class FakePages:
    def __init__(self, *results):
        self.results = list(results)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_client(*results, rec=None, daily_pages=30):
    rec = rec if rec is not None else UntappdRec(pages_date="2026-09-23")
    pages, sleeps = FakePages(*results), []
    client = UntappdClient(rec, daily_pages, NOW, pages, sleep=sleeps.append)
    return client, rec, pages, sleeps


def test_client_returns_html_counts_pages_and_delays_between_pages():
    client, rec, pages, sleeps = make_client(OK, OK)
    assert not client.responded
    assert client.get("https://untappd.com/v/gargoyle-bar/12252462") == "<html>menu</html>"
    assert client.responded and rec.pages_today == 1 and sleeps == []
    client.get("https://untappd.com/v/beatles-pub-yerevan/2162817")
    assert rec.pages_today == 2
    assert len(sleeps) == 1 and 4.0 <= sleeps[0] <= 6.0


def test_client_resets_budget_on_new_yerevan_date():
    rec = UntappdRec(pages_today=30, pages_date="2026-09-22")
    client, rec, _, _ = make_client(OK, rec=rec)
    assert (rec.pages_date, rec.pages_today) == ("2026-09-23", 0)
    client.get("https://untappd.com/v/x/1")
    assert rec.pages_today == 1


def test_client_keeps_counter_on_same_date():
    client, rec, _, _ = make_client(OK, rec=UntappdRec(pages_today=7, pages_date="2026-09-23"))
    client.get("https://untappd.com/v/x/1")
    assert rec.pages_today == 8


def test_client_budget_exhausted():
    client, rec, pages, _ = make_client(OK, rec=UntappdRec(pages_today=2, pages_date="2026-09-23"), daily_pages=3)
    client.get("https://untappd.com/v/x/1")
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/2")
    assert e.value.kind == "budget"
    assert rec.pages_today == 3 and len(pages.urls) == 1
    assert not client.blocked


def test_client_challenge_blocks_the_rest_of_the_run():
    client, rec, pages, _ = make_client(CF, OK)
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/1")
    assert e.value.kind == "cloudflare"
    assert client.blocked and client.responded
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/2")
    assert e.value.kind == "blocked"
    assert len(pages.urls) == 1 and rec.pages_today == 1


def test_client_http_error_is_not_a_block():
    client, _, _, _ = make_client(HttpResponse(404, {}, "not found"), OK)
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/1")
    assert e.value.kind == "http"
    assert not client.blocked and client.responded
    assert client.get("https://untappd.com/v/x/2") == "<html>menu</html>"


def test_client_retries_network_error_once_after_10s():
    client, rec, pages, sleeps = make_client(FetchError("network", "reset"), OK)
    assert client.get("https://untappd.com/v/x/1") == "<html>menu</html>"
    assert sleeps == [10.0]
    assert pages.urls == ["https://untappd.com/v/x/1"] * 2
    assert rec.pages_today == 2 and client.responded


def test_client_second_network_error_fails_without_response():
    client, rec, _, sleeps = make_client(FetchError("network", "a"), FetchError("network", "b"))
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/1")
    assert e.value.kind == "network"
    assert sleeps == [10.0] and rec.pages_today == 2
    assert not client.responded and not client.blocked


def test_client_retry_respects_budget():
    client, rec, pages, _ = make_client(FetchError("network"), OK,
                                        rec=UntappdRec(pages_today=0, pages_date="2026-09-23"), daily_pages=1)
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/1")
    assert e.value.kind == "budget"
    assert rec.pages_today == 1 and len(pages.urls) == 1
