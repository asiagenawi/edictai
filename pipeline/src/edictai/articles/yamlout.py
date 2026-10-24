"""An article as YAML, laid out like the hand-written ones in site/src/content/articles."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

HEADER_RECORDS = (
    "# {type}. Each [n] in the text is a note below; notes name a record by its id, and the\n"
    "# jurisdiction, identifier, title, status, quote and link are read from the data at "
    "build time.\n"
)
HEADER_STUDIES = (
    "# {type}. Each note names a study in the research library and one of its key findings\n"
    "# (by position, from 0); the quote, locator and link are read from the data at build "
    "time.\n"
)


def q(text: Any) -> str:
    """A double-quoted YAML scalar (JSON's string syntax is valid YAML)."""
    return json.dumps(str(text), ensure_ascii=False)


def _cell(c: Any) -> str:
    if isinstance(c, Mapping):
        parts = [f"record: {c['record']}"] + ([f"text: {q(c['text'])}"] if c.get("text") else [])
        return "{ " + ", ".join(parts) + " }"
    return q(c)


def _note(n: Mapping[str, Any]) -> str:
    if "study" in n:
        return f"  - {{ study: {n['study']}, finding: {n['finding']} }}"
    if n.get("provision", 0):
        return f"  - {{ record: {n['record']}, provision: {n['provision']} }}"
    return f"  - record: {n['record']}"


def dump_article(article: Mapping[str, Any]) -> str:
    notes = article.get("notes") or []
    studies_only = notes and all("study" in n for n in notes)
    out = [(HEADER_STUDIES if studies_only else HEADER_RECORDS).format(type=article["type"])]
    keys = (
        "title",
        "dek",
        "type",
        "published",
        "data_release",
        "caption",
        "sketch_note",
        "drawing",
    )
    for k in keys:
        if article.get(k) is not None:
            out.append(f"{k}: {q(article[k])}\n")
    out.append("brief:\n")
    out += [f"  - {q(b)}\n" for b in article.get("brief") or []]
    out.append("body:\n")
    for b in article.get("body") or []:
        if "p" in b:
            out.append(f"  - p: {q(b['p'])}\n")
        elif "h" in b:
            out.append(f"  - h: {q(b['h'])}\n")
        else:
            t = b["table"]
            out += [
                "  - table:\n",
                f"      title: {q(t['title'])}\n",
                f"      source: {q(t['source'])}\n",
                "      columns: [" + ", ".join(q(c) for c in t["columns"]) + "]\n",
                "      rows:\n",
            ]
            out += ["        - [" + ", ".join(_cell(c) for c in row) + "]\n" for row in t["rows"]]
    out.append("notes:\n")
    out += [_note(n) + "\n" for n in notes]
    return "".join(out)
