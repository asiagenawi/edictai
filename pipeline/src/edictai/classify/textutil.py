"""Plain-text extraction from bill documents, and quote matching.

HTML is parsed with the standard library. Text marked as deleted
(`<s>`, `<strike>`, `<del>`, and spans styled line-through) is dropped,
because it is not part of the measure. PDF needs the optional `pypdf`
package (`uv sync --extra classify`); without it a PDF is reported as
unavailable rather than guessed at.
"""

from __future__ import annotations

import io
import re
import unicodedata
from html.parser import HTMLParser

_BLOCK = {
    "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section",
    "article", "pre", "blockquote", "dd", "dt", "hr", "title",
}  # fmt: skip
_SKIP = {"script", "style", "head"}
_DELETED = {"s", "strike", "del"}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._deleted = 0
        self._stack: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        style = " ".join((v or "") for k, v in attrs if k in ("style", "class")).lower()
        struck = tag in _DELETED or "line-through" in style.replace(" ", "")
        if tag in ("br", "hr"):
            self.parts.append("\n")
            return
        self._stack.append((tag, struck))
        if tag in _SKIP:
            self._skip += 1
        if struck:
            self._deleted += 1
        if tag in _BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        # Close back to the matching open tag; tolerate unclosed inner tags.
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                for name, struck in self._stack[i:]:
                    if name in _SKIP:
                        self._skip -= 1
                    if struck:
                        self._deleted -= 1
                del self._stack[i:]
                break
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip or self._deleted:
            return
        self.parts.append(data)


def html_to_text(raw: bytes) -> str:
    text = _decode(raw)
    parser = _TextParser()
    parser.feed(text)
    parser.close()
    return tidy(strip_running_headers(strip_line_numbers(tidy("".join(parser.parts)))))


def _decode(raw: bytes) -> str:
    head = raw[:2048].decode("ascii", "ignore")
    m = re.search(r"charset=[\"']?([A-Za-z0-9_\-]+)", head)
    for enc in ([m.group(1)] if m else []) + ["utf-8", "cp1252", "latin-1"]:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


class PdfUnavailable(Exception):
    """pypdf is not installed, or the PDF has no extractable text."""


def pdf_to_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise PdfUnavailable("pypdf is not installed (uv sync --extra classify)") from None
    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf raises many types on damaged files
        raise PdfUnavailable(f"unreadable PDF ({type(exc).__name__})") from None
    if sum(len(p.strip()) for p in pages) < 200:
        raise PdfUnavailable("PDF has no text layer (scanned image?)")
    text = "\n".join(f"\n[Page {n}]\n{p}" for n, p in enumerate(pages, 1))
    return tidy(strip_running_headers(strip_line_numbers(text)))


def _sequential(lines: list[str], pattern: re.Pattern[str]) -> list[str] | None:
    """Lines with their margin numbers removed, or None when they are not numbered.

    A number counts as a line number when it continues the count (allowing a
    few skipped lines), restarts it at 1, or starts a new run that the next
    numbered line continues. Numbering must cover at least 40% of the lines.
    """
    found = [pattern.search(line) if line.strip() else None for line in lines]
    numbers = [int(m.group("n")) if m else None for m in found]
    expected = 1
    out = list(lines)
    hits = 0
    content = sum(1 for line in lines if line.strip())
    for i, n in enumerate(numbers):
        if n is None:
            continue
        ok = n == 1 or (expected > 1 and expected <= n <= expected + 3)
        if not ok:
            following = [x for x in numbers[i + 1 : i + 5] if x is not None][:2]
            ok = following == [n + 1, n + 2]
        if ok:
            m = found[i]
            out[i] = lines[i][: m.start()] + lines[i][m.end() :]
            expected = n + 1
            hits += 1
    if content < 10 or hits / content < 0.4:
        return None
    return out


_LEADING = re.compile(r"^\s*(?P<n>\d{1,4})(?:\s+|$)")
_TRAILING = re.compile(r"(?:(?<=\s)|(?<=[^\d\s.,;:)]{2})|(?<=[.,;:)]))(?P<n>\d{1,4})\s*$")


def strip_line_numbers(text: str) -> str:
    """Remove margin line numbers (left or right) from one page of a bill."""
    lines = text.splitlines()
    for pattern in (_LEADING, _TRAILING):
        stripped = _sequential(lines, pattern)
        if stripped is not None:
            lines = stripped
    return "\n".join(lines)


def strip_running_headers(text: str) -> str:
    """Drop page furniture: short lines that repeat with only their numbers changing
    ("HB2503 - 4 - LRB104 10601 LNS 20677 b", "Substitute Bill No. 2 LCO 52 of 58").

    A repeated line is dropped only when it sits next to a page break or reads
    like a header (few lowercase letters), so repeated sentences such as
    "NEW SECTION. Sec. 9. A new section is added to chapter 28A.300 RCW" stay.
    """
    lines = text.split("\n")

    def shape(line: str) -> str | None:
        line = line.strip()
        if not line or len(line) > 100 or line.startswith("[Page ") or not re.search(r"\d", line):
            return None
        return re.sub(r"\d+", "#", line)

    near_break: set[int] = set()
    for i, line in enumerate(lines):
        if line.startswith("[Page "):
            for span in (range(i + 1, min(len(lines), i + 6)), range(i - 1, max(-1, i - 6), -1)):
                seen = 0
                for j in span:
                    if lines[j].strip():
                        near_break.add(j)
                        seen += 1
                        if seen == 2:
                            break
    counts: dict[str, int] = {}
    for line in lines:
        key = shape(line)
        if key:
            counts[key] = counts.get(key, 0) + 1
    keep = []
    for i, line in enumerate(lines):
        key = shape(line)
        if key and counts[key] >= 3:
            headerlike = len(re.findall(r"[a-z]", line)) <= 3
            if i in near_break or headerlike:
                continue
        keep.append(line)
    return "\n".join(keep)


def tidy(text: str) -> str:
    """Collapse runs of spaces within lines and runs of blank lines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = text.replace("\u00ad", "")
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    blank = False
    for ln in lines:
        if not ln:
            if out and not blank:
                out.append("")
            blank = True
            continue
        out.append(ln)
        blank = False
    return "\n".join(out).strip() + "\n"


def extract(raw: bytes, mime: str | None) -> str:
    """Plain text of a bill document; raises PdfUnavailable or ValueError."""
    mime = (mime or "").lower()
    if raw[:5] == b"%PDF-" or "pdf" in mime:
        return pdf_to_text(raw)
    if "html" in mime or raw.lstrip()[:1] == b"<":
        return html_to_text(raw)
    if mime.startswith("text/"):
        return tidy(_decode(raw))
    raise ValueError(f"unsupported document type {mime or 'unknown'}")


# -- matching -----------------------------------------------------------------

_FOLD = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
        "\u2015": "-", "\u2212": "-",
    }
)  # fmt: skip


def normalize_for_match(text: str) -> str:
    """Compatibility-fold, unify quotes and dashes, and collapse all whitespace."""
    text = unicodedata.normalize("NFKC", text).translate(_FOLD).replace("\u00ad", "")
    return re.sub(r"\s+", " ", text).strip()


def collapse(text: str) -> str:
    """The text with every whitespace run as one space (how excerpts are shown)."""
    return re.sub(r"\s+", " ", text).strip()


def _squeeze(text: str) -> str:
    # A hyphen before a line break may be a word split across lines ("artifi-
    # cial"); it is dropped on both sides so either spelling matches.
    text = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", normalize_for_match(text))
    return re.sub(r"\s+", "", text)


def quote_in_text(quote: str, text: str) -> bool:
    """True when the quote's characters appear, in order and contiguous, in the text.

    Whitespace is ignored entirely (PDF extraction both splits words,
    "ARTIFICI AL", and joins them), as are typographic quote and dash variants
    and Unicode compatibility forms. Every other character must match.
    """
    q = _squeeze(quote)
    if len(q) < 8:
        return False
    return q in _squeeze(text)
