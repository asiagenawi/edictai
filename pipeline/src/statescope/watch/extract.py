"""Reduce a fetched page to normalized main text plus its links.

HTML is parsed with the standard library: navigation, scripts, styles,
headers, footers and similar chrome are dropped, and when the page marks a
main region (<main>, role="main", or <article>) only that region is kept.
Headings become Markdown-style "#" lines so diffs can name the section they
fall in. PDFs are hashed by their bytes unless the optional `pypdf` package is
installed, in which case their text is extracted and hashed instead.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser

SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "nav",
        "footer",
        "aside",
        "form",
        "iframe",
        "button",
        "select",
        "head",
        "object",
        "canvas",
        "dialog",
    }
)
SKIP_ROLES = frozenset(
    {"navigation", "banner", "contentinfo", "search", "menu", "menubar", "complementary", "dialog"}
)
# Whole class/id tokens (not substrings) that mark page chrome.
SKIP_CLASS_TOKENS = frozenset(
    {"skip-link", "skip-to-content", "screen-reader-text", "site-header", "site-footer"}
)
SKIP_CLASS_PREFIXES = ("breadcrumb", "cookie")
MAIN_TAGS = frozenset({"main", "article"})
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "li",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "table",
        "tr",
        "td",
        "th",
        "thead",
        "tbody",
        "blockquote",
        "pre",
        "figure",
        "figcaption",
        "br",
        "hr",
        "address",
        "details",
        "summary",
        "header",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)
HEADINGS = {f"h{n}": n for n in range(1, 7)}
# Below this much main-region text, the main region is treated as a stub and
# the whole (non-chrome) page is used instead.
MIN_MAIN_CHARS = 200

_AI_TEXT = re.compile(
    r"\bAI\b|\bA\.I\.|artificial[\s\-]*intelligence|generative|\bgen[\s\-]?ai\b|\bchatgpt\b|\bllms?\b",
    re.IGNORECASE,
)
_AI_URL = re.compile(
    r"(?:^|[/\-_.=?&])(?:ai|genai|gen-ai)(?:$|[/\-_.=?&])|artificial[\-_%20+]*intelligence|generative",
    re.IGNORECASE,
)
_DOCUMENT_EXT = re.compile(r"\.(pdf|docx?|pptx?|xlsx?)(?:$|[?#])", re.IGNORECASE)


@dataclass
class Link:
    url: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"url": self.url, "text": self.text}


@dataclass
class Extracted:
    text: str | None  # normalized text; None when only the bytes could be hashed
    sha256: str
    basis: str  # "text" or "bytes"
    kind: str  # "html", "pdf", or "other"
    title: str = ""
    links: list[Link] = field(default_factory=list)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    """NFC, whitespace collapsed within lines, blank lines dropped."""
    text = unicodedata.normalize("NFC", text)
    lines = (re.sub(r"\s+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def looks_like_ai_link(url: str, text: str) -> bool:
    """True when the anchor text or URL mentions AI, artificial intelligence, or generative AI."""
    if _AI_TEXT.search(text or ""):
        return True
    # Path only: query strings carry redirect targets and search terms.
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    return bool(_AI_URL.search(path))


def looks_like_document(url: str) -> bool:
    return bool(_DOCUMENT_EXT.search(url))


class _MainTextParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        # (tag, skip, main) per open element
        self.stack: list[tuple[str, bool, bool]] = []
        self.all_parts: list[str] = []
        self.main_parts: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.in_next_data = False
        self.next_data_parts: list[str] = []
        self.links: list[tuple[Link, bool]] = []  # (link, in_main)
        self._link: tuple[str, list[str], bool] | None = None

    @property
    def skipping(self) -> bool:
        return bool(self.stack) and self.stack[-1][1]

    @property
    def in_main(self) -> bool:
        return bool(self.stack) and self.stack[-1][2]

    def _emit(self, s: str) -> None:
        if self.skipping:
            return
        self.all_parts.append(s)
        if self.in_main:
            self.main_parts.append(s)

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "base" and a.get("href"):
            self.base_url = urllib.parse.urljoin(self.base_url, a["href"])
        if tag == "title":
            self.in_title = True
        if tag == "script" and a.get("id") == "__NEXT_DATA__":
            self.in_next_data = True
        if tag in BLOCK_TAGS:
            self._emit("\n")
        if tag in VOID_TAGS:
            return
        parent_skip = self.skipping
        parent_main = self.in_main
        role = a.get("role", "").lower()
        tokens = (a.get("class", "") + " " + a.get("id", "")).lower().split()
        is_main = tag in MAIN_TAGS or role == "main"
        chrome_class = not is_main and any(
            t in SKIP_CLASS_TOKENS or t.startswith(SKIP_CLASS_PREFIXES) for t in tokens
        )
        skip = parent_skip or (
            tag in SKIP_TAGS
            or role in SKIP_ROLES
            or "hidden" in a
            or a.get("aria-hidden", "").lower() == "true"
            or (tag == "header" and not parent_main)
            or chrome_class
        )
        main = parent_main or is_main
        self.stack.append((tag, skip, main))
        if tag in HEADINGS:
            self._emit("#" * HEADINGS[tag] + " ")
        if tag == "a" and a.get("href") and not skip:
            self._link = (a["href"], [], main)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "script":
            self.in_next_data = False
        if tag == "a" and self._link is not None:
            href, parts, main = self._link
            self._link = None
            url = urllib.parse.urljoin(self.base_url, href.strip())
            url = urllib.parse.urldefrag(url)[0]
            if url.startswith(("http://", "https://")):
                text = re.sub(r"\s+", " ", "".join(parts)).strip()
                self.links.append((Link(url, text), main))
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in BLOCK_TAGS:
            self._emit("\n")

    def handle_data(self, data):
        if self.in_next_data:
            self.next_data_parts.append(data)
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.skipping:
            return
        self._emit(data)
        if self._link is not None:
            self._link[1].append(data)


def _decode(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.IGNORECASE)
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", body[:4096], re.IGNORECASE)
        charset = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    else:
        charset = m.group(1)
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _embedded_content(next_data: str) -> list[str]:
    """HTML `content` fields from a Next.js page's embedded __NEXT_DATA__ JSON.

    Headless-CMS sites (WordPress behind Next.js, for example) send an empty
    body and render it in the browser; the page text is still in the page,
    inside this JSON. Fields are returned in document order, without repeats.
    """
    try:
        data = json.loads(next_data)
    except ValueError:
        return []
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "content" and isinstance(value, str) and "<" in value:
                    if value not in found:
                        found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


def extract_html(html: str, base_url: str, *, _embedded: bool = True) -> Extracted:
    parser = _MainTextParser(base_url)
    parser.feed(html)
    parser.close()
    if _embedded and parser.next_data_parts:
        visible = normalize_text("".join(parser.all_parts))
        if len(visible) < MIN_MAIN_CHARS:
            fragments = _embedded_content("".join(parser.next_data_parts))
            if fragments:
                inner = extract_html(
                    "<main>" + "\n".join(fragments) + "</main>", parser.base_url, _embedded=False
                )
                inner.title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
                return inner
    main = normalize_text("".join(parser.main_parts))
    use_main = len(main) >= MIN_MAIN_CHARS
    text = main if use_main else normalize_text("".join(parser.all_parts))
    seen: set[str] = set()
    links: list[Link] = []
    for link, in_main in parser.links:
        if use_main and not in_main:
            continue
        if link.url in seen:
            continue
        seen.add(link.url)
        links.append(link)
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    return Extracted(text, sha256_text(text), "text", "html", title, links)


def _pdf_text(body: bytes) -> str | None:
    try:
        from pypdf import PdfReader  # optional dependency
    except ImportError:
        return None
    try:
        reader = PdfReader(io.BytesIO(body))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return None


def is_pdf(body: bytes, content_type: str) -> bool:
    return "pdf" in (content_type or "").lower() or body[:5] == b"%PDF-"


def extract(body: bytes, content_type: str, url: str) -> Extracted:
    if is_pdf(body, content_type):
        raw = _pdf_text(body)
        if raw is not None:
            text = normalize_text(raw)
            if text:
                return Extracted(text, sha256_text(text), "text", "pdf")
        return Extracted(None, hashlib.sha256(body).hexdigest(), "bytes", "pdf")
    ct = (content_type or "").lower()
    if not ct or "html" in ct or "xml" in ct or body.lstrip()[:1] == b"<":
        return extract_html(_decode(body, content_type), url)
    if ct.startswith("text/"):
        text = normalize_text(_decode(body, content_type))
        return Extracted(text, sha256_text(text), "text", "other")
    return Extracted(None, hashlib.sha256(body).hexdigest(), "bytes", "other")
