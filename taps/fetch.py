"""Network layer: plain HTTP for shops, a budgeted polite client for Untappd, Cloudflare detection."""
import os
import re
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Collection, Mapping
from urllib.parse import urljoin, urlsplit

import requests

from taps.state import UntappdRec
from taps.timeutil import parse_iso, yerevan_date

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
TIMEOUT = 30            # seconds per plain HTTP request
UNTAPPD_EVERY = timedelta(hours=20)
RETRY_SLEEP = 10.0      # seconds before the single retry after a network error
COUNTRY_BACKFILL_PER_RUN = 40   # product pages a shop adapter may read per run for known items lacking a country


class FetchError(Exception):
    def __init__(self, kind: str, message: str = ""):
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind   # "network" | "cloudflare" | "http" | "budget" | "blocked"


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]    # lower-cased keys
    text: str


PageFetcher = Callable[[str], HttpResponse]


# Not the bare "challenge-platform": Cloudflare's bot-management script /cdn-cgi/challenge-platform/scripts/
# is injected into ordinary 200 pages too. The markers are language-independent except the title.
CHALLENGE_MARKERS = ("_cf_chl_opt", "/cdn-cgi/challenge-platform/h/", "<title>just a moment")


def is_cloudflare_challenge(status: int, headers: Mapping[str, str], body: str) -> bool:
    lowered = body.lower()
    return ("cf-mitigated" in headers
            or any(marker in lowered for marker in CHALLENGE_MARKERS)
            or status == 403)


def _check(resp: HttpResponse, url: str) -> None:
    if is_cloudflare_challenge(resp.status, resp.headers, resp.text):
        raise FetchError("cloudflare", url)
    if resp.status >= 400:
        raise FetchError("http", f"{resp.status} {url}")


class Http:
    def __init__(self, delay: tuple[float, float] = (1.5, 2.0), session: Any = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.delay = delay
        self.session = session if session is not None else requests.Session()
        self.sleep = sleep
        self._first = True

    def _request(self, method: str, url: str, headers: Mapping[str, str] | None, **kw) -> tuple[Any, HttpResponse]:
        if not self._first:
            self.sleep(random.uniform(*self.delay))
        self._first = False
        try:
            raw = getattr(self.session, method)(url, headers={"User-Agent": UA, **(headers or {})},
                                                timeout=TIMEOUT, **kw)
            resp = HttpResponse(raw.status_code, {k.lower(): v for k, v in raw.headers.items()}, raw.text)
        except requests.RequestException as e:
            raise FetchError("network", f"{url}: {e}") from e
        _check(resp, url)
        return raw, resp

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> HttpResponse:
        return self._request("get", url, headers)[1]

    def post_json(self, url: str, payload: Any, headers: Mapping[str, str] | None = None) -> Any:
        raw, _ = self._request("post", url, headers, json=payload)
        try:
            return raw.json()
        except ValueError as e:   # requests' JSONDecodeError is a ValueError
            raise FetchError("http", f"invalid JSON from {url}") from e


def shop_photo(src: str | None, base: str, hosts: Collection[str]) -> str | None:
    """A shop's product photo: `src` (absolute or relative to `base`) if it is https on one of the shop's own hosts."""
    src = (src or "").strip()
    if not src:
        return None
    url = urljoin(base, src)
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname in hosts else None


def untappd_due(untappd: UntappdRec, now: datetime) -> bool:
    return untappd.last_attempt is None or now - parse_iso(untappd.last_attempt) >= UNTAPPD_EVERY


class UntappdClient:
    def __init__(self, untappd: UntappdRec, daily_pages: int, now: datetime,
                 fetch_page: PageFetcher, delay: tuple[float, float] = (4.0, 6.0),
                 sleep: Callable[[float], None] = time.sleep):
        self.untappd = untappd
        self.daily_pages = daily_pages
        self.fetch_page = fetch_page
        self.delay = delay
        self.sleep = sleep
        self.blocked = False
        self.responded = False
        self.last_html: str | None = None   # last page fetched, for TAPS_DEBUG_DIR (dump_debug_html)
        self.last_url: str | None = None
        self._first = True
        today = yerevan_date(now)
        if untappd.pages_date != today:
            untappd.pages_date = today
            untappd.pages_today = 0

    def _attempt(self, url: str) -> HttpResponse:
        if self.untappd.pages_today >= self.daily_pages:
            raise FetchError("budget", url)
        self.untappd.pages_today += 1
        return self.fetch_page(url)

    def get(self, url: str) -> str:
        if self.blocked:
            raise FetchError("blocked", url)
        if not self._first:
            self.sleep(random.uniform(*self.delay))
        self._first = False
        try:
            resp = self._attempt(url)
        except FetchError as e:
            if e.kind != "network":
                raise
            self.sleep(RETRY_SLEEP)
            resp = self._attempt(url)
        self.responded = True
        if is_cloudflare_challenge(resp.status, resp.headers, resp.text):
            self.blocked = True
        _check(resp, url)
        self.last_html, self.last_url = resp.text, f"{url} -> {resp.headers['x-final-url']}" if resp.headers.get("x-final-url") else url
        return resp.text


DEBUG_DIR_ENV = "TAPS_DEBUG_DIR"


def dump_debug_html(source_key: str, client: "UntappdClient") -> None:
    """Opt-in capture: when TAPS_DEBUG_DIR is set, write the last page client fetched to
    <TAPS_DEBUG_DIR>/<source_key, ':' -> '_'>.html, for debugging a production-only failure
    (e.g. Cloudflare blocks Untappd from a dev machine). A no-op if the env var is unset, or if
    the client never fetched a page."""
    debug_dir = os.environ.get(DEBUG_DIR_ENV)
    if not debug_dir or client.last_html is None:
        return
    path = Path(debug_dir)
    path.mkdir(parents=True, exist_ok=True)
    body = f"<!-- {client.last_url} -->\n{redact_users(client.last_html)}" if client.last_url else redact_users(client.last_html)
    (path / f"{source_key.replace(':', '_')}.html").write_text(body, encoding="utf-8")


def redact_users(html: str) -> str:
    """The repo is public and so are its workflow artifacts: strip Untappd user handles, display
    names in user links, avatar alts and profile photos before a page is written to disk."""
    html = re.sub(r"/user/[^\"'/?#\s<>]+", "/user/anon", html)
    html = re.sub(r'data-user-name="[^"]*"', 'data-user-name="anon"', html)
    html = re.sub(r'(<a\b[^>]*href="[^"]*/user/anon[^"]*"[^>]*>)[^<]*(</a>)', r"\1User\2", html)
    html = re.sub(r'(<a\b[^>]*href="[^"]*/user/anon[^"]*"[^>]*)title="[^"]*"', r'\1title="User"', html)
    html = re.sub(r'alt="[^"]*"', 'alt=""', html)
    return re.sub(r'https?://[^"\s]*(?:assets\.untappd\.com/profile|untappd\.s3\.amazonaws\.com/photos)/[^"\s]+', "", html)


# Ported from hopandshot/hopsandshot scraper.py (languages switched to en-US).
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""


def playwright_fetcher() -> tuple[PageFetcher, Callable[[], None]]:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=UA,
            locale="en-US",
            viewport={"width": 1366, "height": 768},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        context.add_init_script(STEALTH_INIT_SCRIPT)
        page = context.new_page()
    except BaseException:
        pw.stop()
        raise

    def fetch_page(url: str) -> HttpResponse:
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1500)
            if resp is None:
                raise FetchError("network", f"no response: {url}")
            headers = {k.lower(): v for k, v in resp.headers.items()}
            headers["x-final-url"] = page.url   # after redirects; shown in TAPS_DEBUG_DIR dumps
            return HttpResponse(resp.status, headers, page.content())
        except PlaywrightError as e:
            raise FetchError("network", f"{url}: {e}") from e

    def close() -> None:
        try:
            context.close()
            browser.close()
        finally:
            pw.stop()

    return fetch_page, close
