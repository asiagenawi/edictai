"""Minimal HTTP plumbing shared by the API clients.

Requests go through a `Transport` callable so tests can substitute a fake and
never touch the network. Error messages never include query strings, because
API keys travel in them.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping

USER_AGENT = "edictai-pipeline/0.1 (+https://github.com/asiagenawi/edictai)"

# (url, params, headers) -> response body
Transport = Callable[[str, Mapping[str, str], Mapping[str, str]], bytes]


class HTTPError(Exception):
    """A failed request, described without its query string."""


def redact(url: str) -> str:
    """The URL without its query string."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def urllib_transport(timeout: float = 120.0) -> Transport:
    def get(url: str, params: Mapping[str, str], headers: Mapping[str, str]) -> bytes:
        full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
        request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, **headers})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise HTTPError(f"HTTP {exc.code} from {redact(url)}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise HTTPError(f"request to {redact(url)} failed: {reason}") from None

    return get


class RateLimiter:
    """Spaces calls at least `interval` seconds apart."""

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.interval = interval
        self.clock = clock
        self.sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self.interval - (self.clock() - self._last)
            if remaining > 0:
                self.sleep(remaining)
        self._last = self.clock()
