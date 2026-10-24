"""Polite HTTP fetching: robots.txt, per-host spacing, conditional requests.

Requests go through a `Transport` callable so tests can substitute a fake.
A page that answers with a block (401/403/429 or a bot challenge) is recorded
as blocked and its host is left alone for the rest of the run; the watcher
never retries around a block.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

ROBOTS_TOKEN = "EdictaiWatch"
USER_AGENT = (
    f"{ROBOTS_TOKEN}/0.1 (Edictai, formerly StateScope, policy-page change watcher; "
    "+https://github.com/asiagenawi/edictai)"
)
MAX_BYTES = 20 * 1024 * 1024
BLOCK_STATUSES = frozenset({401, 403, 429})
# Markers that only appear on bot-challenge pages, whatever the status code.
CHALLENGE_PAGE_MARKERS = (
    b"<title>just a moment...</title>",
    b"<title>attention required! | cloudflare</title>",
    b"<title>radware captcha page</title>",
    b"<title>azure waf</title>",
    b"/.azwaf/captcha",
    b"validate.perfdrive.com",
    b"incapsula incident id",
    b"captcha-delivery.com",
    b"made us think that you are a bot",
)
# Markers that CDNs also inject into ordinary pages; they count only on an error status.
CHALLENGE_ERROR_MARKERS = (
    b"challenge-platform",
    b"cf-browser-verification",
    b"_incapsula_resource",
    b"px-captcha",
)


@dataclass
class Response:
    status: int
    headers: Mapping[str, str]  # lower-case names
    body: bytes
    url: str  # final URL after redirects


# (url, headers, timeout) -> Response. Raises OSError (or a subclass) on network failure.
Transport = Callable[[str, Mapping[str, str], float], Response]


def _lower(headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()} if headers else {}


def urllib_transport(max_bytes: int = MAX_BYTES) -> Transport:
    def get(url: str, headers: Mapping[str, str], timeout: float) -> Response:
        request = urllib.request.Request(url, headers=dict(headers))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise OSError(f"response larger than {max_bytes} bytes")
                return Response(response.status, _lower(response.headers), body, response.geturl())
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(64 * 1024)
            except Exception:
                body = b""
            return Response(exc.code, _lower(exc.headers), body, exc.geturl() or url)

    return get


@dataclass
class FetchResult:
    url: str
    # ok | not-modified | robots-disallowed | robots-unavailable | blocked | deferred | error
    outcome: str
    status: int | None = None
    body: bytes = b""
    content_type: str = ""
    etag: str | None = None
    last_modified: str | None = None
    final_url: str | None = None
    error: str | None = None


def host_key(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


@dataclass
class _Host:
    robots: urllib.robotparser.RobotFileParser | None = None
    robots_status: str = ""  # parsed | allow-all | disallow-all | unavailable | blocked
    last_request: float | None = None  # any request, robots.txt included
    last_page: float | None = None  # page requests only
    crawl_delay: float = 0.0
    robots_error: str = ""
    blocked: str | None = None


@dataclass
class Fetcher:
    transport: Transport = field(default_factory=urllib_transport)
    user_agent: str = USER_AGENT
    interval: float = 1.0  # minimum seconds between any two requests to one host
    timeout: float = 30.0
    # A robots.txt Crawl-delay is honored between page requests to a host. When
    # honoring it would mean waiting longer than this, the page is deferred to
    # the next run instead of stalling the whole run.
    max_wait: float = 60.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _hosts: dict[str, _Host] = field(default_factory=dict)

    # -- plumbing ----------------------------------------------------------
    def _host(self, url: str) -> _Host:
        key = host_key(url)
        if key not in self._hosts:
            self._hosts[key] = _Host()
        return self._hosts[key]

    def host_info(self, url: str) -> _Host:
        """What the fetcher knows about a URL's host (robots status, delays, blocks)."""
        return self._host(url)

    def _wait_needed(self, host: _Host, page: bool) -> float:
        now = self.clock()
        wait = 0.0
        if host.last_request is not None:
            wait = self.interval - (now - host.last_request)
        if page and host.last_page is not None and host.crawl_delay:
            wait = max(wait, host.crawl_delay - (now - host.last_page))
        return max(wait, 0.0)

    def _request(self, url: str, headers: Mapping[str, str], *, page: bool = False) -> Response:
        host = self._host(url)
        wait = self._wait_needed(host, page)
        if wait > 0:
            self.sleep(wait)
        try:
            return self.transport(url, {"User-Agent": self.user_agent, **headers}, self.timeout)
        finally:
            host.last_request = self.clock()
            if page:
                host.last_page = host.last_request

    # -- robots.txt --------------------------------------------------------
    def _load_robots(self, url: str) -> _Host:
        host = self._host(url)
        if host.robots_status:
            return host
        robots_url = host_key(url) + "/robots.txt"
        parser = urllib.robotparser.RobotFileParser(robots_url)
        try:
            response = self._request(robots_url, {})
        except (OSError, ValueError) as exc:
            host.robots_status = "unavailable"
            host.robots_error = str(getattr(exc, "reason", exc))
            return host
        if _is_challenge(response):
            host.robots_status = "blocked"
            host.blocked = f"robots.txt answered with a bot challenge (HTTP {response.status})"
            return host
        if response.status == 200:
            text = response.body.decode("utf-8", errors="replace")
            parser.parse(text.splitlines())
            host.robots_status = "parsed"
            delay = parser.crawl_delay(ROBOTS_TOKEN)
            if delay:
                host.crawl_delay = float(delay)
        elif response.status in (401, 403):
            parser.disallow_all = True
            host.robots_status = "disallow-all"
            host.blocked = f"robots.txt answered HTTP {response.status}"
        elif 400 <= response.status < 500:
            parser.allow_all = True
            host.robots_status = "allow-all"
        else:
            # RFC 9309: an unreachable robots.txt means assume full disallow.
            host.robots_status = "unavailable"
            host.robots_error = f"HTTP {response.status}"
        host.robots = parser
        return host

    def allowed(self, url: str) -> bool:
        host = self._load_robots(url)
        if host.robots_status in ("unavailable", "blocked") or host.robots is None:
            return False
        return host.robots.can_fetch(ROBOTS_TOKEN, url)

    # -- pages -------------------------------------------------------------
    def get(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        host = self._host(url)
        if host.blocked:
            return FetchResult(url, "blocked", error=f"host skipped: {host.blocked}")
        if not self.allowed(url):
            if host.blocked:
                return FetchResult(url, "blocked", error=host.blocked)
            if host.robots_status == "unavailable":
                return FetchResult(
                    url,
                    "robots-unavailable",
                    error=f"robots.txt could not be read: {host.robots_error}",
                )
            return FetchResult(url, "robots-disallowed", error="disallowed by robots.txt")
        headers: dict[str, str] = {
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
        }
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        wait = self._wait_needed(host, page=True)
        if wait > self.max_wait:
            return FetchResult(
                url,
                "deferred",
                error=f"robots.txt Crawl-delay {host.crawl_delay:g}s; left for the next run",
            )
        try:
            response = self._request(url, headers, page=True)
        except (OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            return FetchResult(url, "error", error=f"request failed: {reason}")

        h = response.headers
        result = FetchResult(
            url,
            "ok",
            status=response.status,
            content_type=h.get("content-type", ""),
            etag=h.get("etag"),
            last_modified=h.get("last-modified"),
            final_url=response.url,
        )
        if response.status == 304:
            result.outcome = "not-modified"
            result.etag = result.etag or etag
            result.last_modified = result.last_modified or last_modified
            return result
        if response.status in BLOCK_STATUSES or _is_challenge(response):
            result.outcome = "blocked"
            result.error = f"HTTP {response.status}" + (
                " (bot challenge)" if _is_challenge(response) else ""
            )
            host.blocked = result.error
            return result
        if not 200 <= response.status < 300:
            result.outcome = "error"
            result.error = f"HTTP {response.status}"
            return result
        result.body = response.body
        return result


def _is_challenge(response: Response) -> bool:
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    head = response.body[:50000].lower()
    if any(marker in head for marker in CHALLENGE_PAGE_MARKERS):
        return True
    return response.status >= 400 and any(m in head for m in CHALLENGE_ERROR_MARKERS)
