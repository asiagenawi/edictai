"""Change watcher for the guidance pages listed in organizations' `watch_urls`.

Each page is fetched politely (robots.txt, one request per second per host,
conditional requests), reduced to its main text, and hashed. A changed hash
yields a change entry with a trimmed diff, any new links that look like AI
policy documents, and proposed `revisions[]` entries for document records that
cite the page. Nothing under data/ is written; proposals are returned for a
person to review.
"""

from .extract import Extracted, extract, extract_html, looks_like_ai_link
from .fetch import USER_AGENT, Fetcher, FetchResult, Response, Transport, urllib_transport
from .mapping import RevisionProposal, find_documents_for_url, normalize_url, propose_revisions
from .report import ChangeEntry, WatchReport, render_markdown
from .run import run
from .state import WatchState

__all__ = [
    "USER_AGENT",
    "ChangeEntry",
    "Extracted",
    "FetchResult",
    "Fetcher",
    "Response",
    "RevisionProposal",
    "Transport",
    "WatchReport",
    "WatchState",
    "extract",
    "extract_html",
    "find_documents_for_url",
    "looks_like_ai_link",
    "normalize_url",
    "propose_revisions",
    "render_markdown",
    "run",
    "urllib_transport",
]
