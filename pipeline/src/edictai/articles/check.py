"""Checks an article must pass before it is written.

The first group repeats what the site's content schema (site/src/content.config.ts)
enforces at build time: the fields and their types, notes that resolve to a record's
provision or a study's finding, [n] markers that match the notes, and table rows that
fit their columns and name records that exist. The rest are stricter: notes and table
cells may cite only the records and studies the model was given; quoted words must be
copied from a quote or title in that data; every number must appear in it; the body's
length must be in range; the house style's hard rules (no questions, no em dashes,
no listed hype words) hold; the first paragraph carries at most `MAX_OPENING_STATS`
statistics; and the last paragraph is not mostly counts.

`advisories` are softer: they are reported, and passed to the editor when a repair
runs, but do not stop the article.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .context import Context

DRAWINGS = ("map", "desk", "capitol", "gavel", "classroom", "phone", "calendar", "lock")
TYPES = ("Explainer", "Analysis", "Roundup", "State brief", "Research digest")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MARKER = re.compile(r"\[(\d+)\]")
QUOTED = re.compile(r"“([^”]+)”|\"([^\"]+)\"")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
WORD = re.compile(r"\S+")
MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|"
    "December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
# What a number that is not a statistic follows: a bill or rule identifier (AB 1159,
# H.R. 10719, S. 5225, Executive Order 14110, Rule 6A-1.0957), a month (September 10),
# a year or letter and a hyphen (2024-25, K-12).
NOT_A_STAT_AFTER = re.compile(
    r"(?:\b[A-Z][A-Z.]{0,4}|\b(?:Order|No\.|Section|Sec\.|Rule|Chapter|Act|Law|Title|"
    rf"Part|Grade|grade|grades|{MONTHS})|\d{{4}}[-\u2013]|\b[A-Za-z][-\u2013])\s?$"
)
NUMBER_WORDS = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|hundred|dozen)\b",
    re.IGNORECASE,
)
SELF_REFERENCE = re.compile(
    r"^(?:this|the) (?:explainer|analysis|brief|digest|roundup|article|piece)\b", re.I
)
MAX_OPENING_STATS = 2  # numerals that are statistics, in the first paragraph
CLOSING_COUNTS = 4  # a last paragraph with this many counts...
CLOSING_SHARE = 0.12  # ...making up this share of its words reads as a tally
BANNED = (
    "landmark",
    "sweeping",
    "groundbreaking",
    "game-changing",
    "game changer",
    "unprecedented",
    "pivotal",
    "crucial",
    "robust",
    "navigate",
    "navigating",
    "landscape",
    "delve",
    "tapestry",
    "ever-evolving",
    "rapidly evolving",
    "it is worth noting",
    "it's worth noting",
    "importantly,",
    "notably,",
    "in conclusion",
    "a growing number",
)


def from_reply(
    reply: Mapping[str, Any], *, type: str, published: str, release: str
) -> tuple[str, dict[str, Any]]:
    """(slug, article in the content schema's shape) from a structured reply."""

    def cell(c: Any) -> Any:
        if not isinstance(c, Mapping):
            return c
        if not c.get("record"):
            return str(c.get("text") or "")
        return {"record": c["record"], **({"text": c["text"]} if c.get("text") else {})}

    body = []
    for b in reply.get("body") or []:
        if isinstance(b, Mapping) and "table" in b and isinstance(b["table"], Mapping):
            t = b["table"]
            body.append(
                {
                    "table": {
                        "title": t.get("title"),
                        "source": t.get("source"),
                        "columns": list(t.get("columns") or []),
                        "rows": [[cell(c) for c in row] for row in t.get("rows") or []],
                    }
                }
            )
        else:
            body.append(dict(b) if isinstance(b, Mapping) else b)
    notes = []
    for n in reply.get("notes") or []:
        if isinstance(n, Mapping) and "record" in n:
            notes.append({"record": n["record"], "provision": int(n.get("provision") or 0)})
        elif isinstance(n, Mapping) and "study" in n:
            notes.append({"study": n["study"], "finding": int(n.get("finding") or 0)})
        else:
            notes.append(n)
    article = {
        "title": reply.get("title"),
        "dek": reply.get("dek"),
        "type": type,
        "published": published,
        "data_release": release,
        "caption": reply.get("caption"),
        "sketch_note": reply.get("sketch_note") or "",
        "brief": list(reply.get("brief") or []),
        "body": body,
        "notes": notes,
    }
    return str(reply.get("slug") or ""), article


def paragraphs(article: Mapping[str, Any]) -> list[str]:
    return [b["p"] for b in article.get("body") or [] if isinstance(b, Mapping) and "p" in b]


def word_count(article: Mapping[str, Any]) -> int:
    return sum(len(WORD.findall(MARKER.sub("", p))) for p in paragraphs(article))


def _norm(text: str) -> str:
    for curly, straight in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"')):
        text = text.replace(curly, straight)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text.strip(" .,;:")


def _num(token: str) -> str:
    token = token.replace(",", "")
    if "." in token:
        return str(float(token))
    return str(int(token))


def statistics_in(text: str) -> list[str]:
    """Numerals that state a quantity: not years, dates, identifiers or grade spans."""
    text = MARKER.sub(" ", text)
    out = []
    for m in NUMBER.finditer(text):
        token = m.group(0).rstrip(",")
        if re.fullmatch(r"(?:19|20)\d\d", token):
            continue
        if NOT_A_STAT_AFTER.search(text[max(0, m.start() - 24) : m.start()]):
            continue
        out.append(token)
    return out


def counts_in(text: str) -> int:
    """Statistics plus spelled-out numbers ("five", "twelve")."""
    return len(statistics_in(text)) + len(NUMBER_WORDS.findall(MARKER.sub(" ", text)))


def numbers_in(text: str) -> set[str]:
    return {_num(t) for t in NUMBER.findall(MARKER.sub(" ", text))}


def _prose(article: Mapping[str, Any]) -> list[tuple[str, str]]:
    out = [("title", str(article.get("title") or "")), ("dek", str(article.get("dek") or ""))]
    out += [(f"brief {i + 1}", str(b)) for i, b in enumerate(article.get("brief") or [])]
    for i, b in enumerate(article.get("body") or []):
        if isinstance(b, Mapping) and "p" in b:
            out.append((f"paragraph {i + 1}", str(b["p"])))
        elif isinstance(b, Mapping) and "h" in b:
            out.append((f"heading {i + 1}", str(b["h"])))
    return out


def _table_text(article: Mapping[str, Any]) -> list[str]:
    out = []
    for b in article.get("body") or []:
        if isinstance(b, Mapping) and isinstance(b.get("table"), Mapping):
            t = b["table"]
            out += [str(t.get("title") or ""), *map(str, t.get("columns") or [])]
            for row in t.get("rows") or []:
                for c in row:
                    out.append(c if isinstance(c, str) else str(c.get("text") or ""))
    return out


def check_article(
    slug: str,
    article: Mapping[str, Any],
    ctx: Context,
    *,
    type: str,
    published: str,
    existing_slugs: Iterable[str],
    min_words: int,
    max_words: int,
) -> list[str]:
    errors: list[str] = []
    err = errors.append

    # -- the content schema --------------------------------------------------------
    for k in ("title", "dek", "data_release", "caption"):
        if not isinstance(article.get(k), str) or not article[k].strip():
            err(f"{k} must be a non-empty string")
    if "sketch_note" in article and not str(article["sketch_note"] or "").strip():
        err("sketch_note, when given, must be a non-empty string")
    if "drawing" in article and article["drawing"] not in DRAWINGS:
        err(f"drawing must be one of {', '.join(DRAWINGS)}")
    if article.get("type") not in TYPES:
        err(f"type {article.get('type')!r} is not an article type the run writes")
    if article.get("type") != type:
        err(f"type must be {type}")
    if article.get("published") != published or not DATE.match(str(published)):
        err(f"published must be {published}")
    if "author" in article:
        err("only Opinion pieces carry an author")
    brief = article.get("brief")
    if not isinstance(brief, list) or not 2 <= len(brief) <= 5:
        err("brief needs two to five key points")
    elif not all(isinstance(b, str) and b.strip() for b in brief):
        err("every key point must be a non-empty string")
    body = article.get("body")
    if not isinstance(body, list) or not body:
        err("body must have at least one block")
        body = []
    notes = article.get("notes")
    if not isinstance(notes, list):
        err("notes must be a list")
        notes = []

    for i, b in enumerate(body):
        if not isinstance(b, Mapping) or len(b) != 1 or next(iter(b)) not in ("p", "h", "table"):
            err(f"body block {i + 1} must be exactly one of p, h, or table")
            continue
        if "table" in b:
            t = b["table"]
            if not isinstance(t, Mapping):
                err(f"body block {i + 1}: table must be an object")
                continue
            cols = t.get("columns") or []
            rows = t.get("rows") or []
            if not t.get("title") or not t.get("source") or not cols or not rows:
                err(f"table in block {i + 1} needs a title, source, columns, and rows")
            for ri, row in enumerate(rows):
                if len(row) != len(cols):
                    err(f"table row {ri + 1} has {len(row)} cells for {len(cols)} columns")
                for c in row:
                    if isinstance(c, str):
                        continue
                    rid = c.get("record") if isinstance(c, Mapping) else None
                    rec = ctx.records.get(rid) if rid else None
                    if rec is None:
                        err(f"table cell cites record {rid!r}, which is not in the data given")
                    elif not rec.identifier and not c.get("text"):
                        err(f"record {rid!r} has no identifier; give the table cell a text")
        elif not isinstance(b[next(iter(b))], str) or not b[next(iter(b))].strip():
            err(f"body block {i + 1} must be a non-empty string")

    for i, n in enumerate(notes):
        if not isinstance(n, Mapping):
            err(f"note {i + 1} must cite a record or a study")
        elif "record" in n:
            rec = ctx.records.get(n["record"])
            idx = n.get("provision", 0)
            if rec is None:
                err(f"note {i + 1} cites record {n['record']!r}, which is not in the data given")
            elif not isinstance(idx, int) or not 0 <= idx < len(rec.provisions):
                err(f"note {i + 1}: record {n['record']!r} has no provision {idx}")
        elif "study" in n:
            st = ctx.studies.get(n["study"])
            idx = n.get("finding")
            if st is None:
                err(f"note {i + 1} cites study {n['study']!r}, which is not in the data given")
            elif not isinstance(idx, int) or not 0 <= idx < len(st.findings):
                err(f"note {i + 1}: study {n['study']!r} has no key finding {idx}")
        else:
            err(f"note {i + 1} must cite a record or a study")

    cited: set[int] = set()
    for p in paragraphs(article):
        cited |= {int(m) for m in MARKER.findall(p)}
    for n in sorted(cited):
        if not 1 <= n <= len(notes):
            err(f"the text cites [{n}] but there are {len(notes)} notes")
    for i in range(len(notes)):
        if i + 1 not in cited:
            err(f"note {i + 1} is never cited in the text")
    for where, text in _prose(article):
        if not where.startswith("paragraph") and MARKER.search(text):
            err(f"{where} has a note marker; markers go only in paragraphs")

    # -- stricter rules --------------------------------------------------------------
    if not SLUG.match(slug) or len(slug) > 80:
        err(f"slug {slug!r} must be lowercase words joined by hyphens")
    elif slug in set(existing_slugs):
        err(f"slug {slug!r} is already used by an article")

    words = word_count(article)
    if not min_words <= words <= max_words:
        err(f"the body paragraphs run {words} words; they must run {min_words} to {max_words}")

    corpus_quotes = _norm(
        "\n".join(
            [p.quote or "" for r in ctx.records.values() for p in r.provisions]
            + [f.quote or "" for s in ctx.studies.values() for f in s.findings]
            + [r.title for r in ctx.records.values()]
            + [s.title for s in ctx.studies.values()]
        )
    )
    known = numbers_in(json.dumps(ctx.payload, ensure_ascii=False))
    known |= numbers_in(f"{ctx.as_of} {ctx.release} {published}")
    for where, text in _prose(article) + [("table", t) for t in _table_text(article)]:
        for m in QUOTED.finditer(text):
            span = _norm(m.group(1) or m.group(2) or "")
            if span and span not in corpus_quotes:
                err(f"{where} quotes words not found in any quote or title given: {span[:80]!r}")
        for num in sorted(numbers_in(text) - known):
            err(f"{where} has the number {num}, which the data given does not contain")
        low = text.lower()
        for phrase in BANNED:
            if re.search(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", low):
                err(f"{where} uses {phrase.strip(',')!r}, which the house style rules out")
        if "?" in text:
            err(f"{where} asks a question; state facts instead")
        if "—" in text or "!" in text:
            err(f"{where} uses an em dash or exclamation mark")
    paras = paragraphs(article)
    if paras:
        stats = statistics_in(paras[0])
        if len(stats) > MAX_OPENING_STATS:
            err(
                f"the first paragraph has {len(stats)} statistics ({', '.join(stats)}); "
                f"open with what the law or study does, with at most one number, and move "
                "the counts later"
            )
        last = paras[-1]
        n, words = counts_in(last), len(WORD.findall(MARKER.sub("", last)))
        if len(paras) > 1 and n >= CLOSING_COUNTS and n >= CLOSING_SHARE * words:
            err(
                f"the last paragraph is mostly counts ({n} numbers in {words} words); end "
                "on what happens next or the most significant implication instead"
            )
    dek_words = len(WORD.findall(str(article.get("dek") or "")))
    if dek_words > 60:
        err(f"the dek runs {dek_words} words; keep it under 60")
    title_words = len(WORD.findall(str(article.get("title") or "")))
    if title_words > 14:
        err(f"the title runs {title_words} words; keep it to 12 or so")
    return errors


def advisories(article: Mapping[str, Any], *, max_words: int, target_words: int) -> list[str]:
    """Soft checks: reported and passed to the editor on a repair, never blocking."""
    out = []
    words = word_count(article)
    soft_max = target_words + (max_words - target_words) // 2
    if words > soft_max:
        out.append(
            f"the body runs {words} words, near the ceiling of {max_words}; aim for about "
            f"{target_words} by cutting minor records rather than compressing sentences"
        )
    paras = paragraphs(article)
    if paras and SELF_REFERENCE.match(paras[0].strip()):
        out.append(
            "the first sentence describes the article; lead with what the law, rule or "
            "study does and who it affects"
        )
    return out
