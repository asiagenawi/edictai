"""Reviewer instructions and output schemas for the two classification stages.

The scope definition is copied from docs/methodology.md (the Scope section,
including the borderline-case table and the education levels) each time the
files are generated, and the topic and provision codes come from data/vocab,
so the instructions cannot drift from the published methodology.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..paths import Paths
from ..vocab import Vocab

TRIAGE_DECISIONS = ("in", "out", "needs_text")
CONFIDENCE = ("high", "medium", "low")
TRIAGE_REASON_WORDS = 20
FULL_REASON_WORDS = 60
SUMMARY_MAX_SENTENCES = 3
QUOTE_MAX = 300
# Words a neutral summary does not use.
LOADED_WORDS = (
    "landmark", "historic", "groundbreaking", "sweeping", "controversial", "strong", "weak",
    "robust", "critical", "crucial", "important", "vital", "dangerous", "harmful", "common-sense",
    "commonsense", "unprecedented", "bold", "ambitious", "flawed", "misguided",
)  # fmt: skip


def scope_section(methodology: Path) -> str:
    """The '## Scope' section of the methodology, without its heading."""
    text = methodology.read_text(encoding="utf-8")
    m = re.search(r"^## Scope\s*\n(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL)
    if not m:
        raise ValueError(f"{methodology}: no '## Scope' section")
    body = m.group(1).strip()
    # Demote headings so they nest under the instruction file's own sections.
    return re.sub(r"^(#+) ", lambda h: "#" * (len(h.group(1)) + 1) + " ", body, flags=re.M)


def _topics_table(vocab: Vocab) -> str:
    rows = ["| code | covers | levels |", "|---|---|---|"]
    for t in vocab.topics:
        if t.get("deprecated"):
            continue
        levels = ", ".join(t.get("education_levels") or [])
        rows.append(f"| `{t['code']}` | {t.get('description') or t['name']} | {levels} |")
    return "\n".join(rows)


def _provisions_table(vocab: Vocab) -> str:
    rows = ["| code | what the clause does | topic | levels |", "|---|---|---|---|"]
    for p in vocab.provision_types:
        if p.get("deprecated"):
            continue
        levels = ", ".join(p.get("education_levels") or [])
        rows.append(f"| `{p['code']}` | {p['name']} | `{p.get('topic', '')}` | {levels} |")
    return "\n".join(rows)


def _active(items: list[dict[str, Any]]) -> list[str]:
    return [i["code"] for i in items if not i.get("deprecated")]


# -- output schemas -----------------------------------------------------------


def triage_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Triage judgment (one JSON object per line)",
        "type": "object",
        "required": ["key", "decision", "reason"],
        "additionalProperties": False,
        "properties": {
            "key": {"type": "string", "minLength": 1},
            "decision": {"enum": list(TRIAGE_DECISIONS)},
            "reason": {"type": "string", "minLength": 3, "maxLength": 200},
        },
    }


def full_schema(vocab: Vocab) -> dict[str, Any]:
    levels = sorted(vocab.codes("education_levels"))
    provision = {
        "type": "object",
        "required": ["type", "description", "locator", "quote"],
        "additionalProperties": False,
        "properties": {
            "type": {"enum": _active(vocab.provision_types)},
            "description": {"type": "string", "minLength": 10, "maxLength": 400},
            "locator": {"type": "string", "minLength": 1, "maxLength": 120},
            "quote": {"type": "string", "minLength": 10, "maxLength": QUOTE_MAX},
            "education_levels": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"enum": levels},
            },
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Full judgment (one JSON object per line)",
        "type": "object",
        "required": ["key", "in_scope", "confidence", "reasons"],
        "additionalProperties": False,
        "properties": {
            "key": {"type": "string", "minLength": 1},
            "in_scope": {"type": "boolean"},
            "confidence": {"enum": list(CONFIDENCE)},
            "education_levels": {
                "type": "array",
                "uniqueItems": True,
                "items": {"enum": levels},
            },
            "topics": {
                "type": "array",
                "uniqueItems": True,
                "items": {"enum": _active(vocab.topics)},
            },
            "summary": {"type": "string", "maxLength": 700},
            "provisions": {"type": "array", "items": provision},
            "reasons": {"type": "string", "minLength": 3, "maxLength": 500},
        },
        "if": {"properties": {"in_scope": {"const": True}}},
        "then": {
            "required": ["education_levels", "topics", "summary", "provisions"],
            "properties": {
                "education_levels": {"minItems": 1},
                "topics": {"minItems": 1},
                "summary": {"minLength": 20},
            },
        },
    }


# -- instruction files --------------------------------------------------------

_DECISION_RULES = """\
- Decide on what the measure does, not on its title. A title that says "artificial
  intelligence" is not enough, and a title that does not mention AI does not rule a
  bill out.
- A measure is in scope when **any** of its sections meets the definition, including
  one education section inside a general AI bill.
- Minors, children, or youth alone are not enough: the provision must name schools,
  students, educators, school districts, education agencies, or institutions of higher
  education (colleges, universities, community colleges).
- "AI" must mean artificial intelligence. Artificial insemination, the Hawaiian word
  *ai*, paragraph designators such as `ai.`, and other acronyms are out of scope.
- Education technology, screen-time, cell-phone, or student-data measures that never
  address AI are out of scope.
- General government AI-use, election deepfake, consumer, or employment AI bills are
  out of scope unless a provision applies to education (for example, a list of covered
  decisions that includes education, or a duty placed on schools).
- Only public, system-level policy counts. Measures aimed at one named campus are
  still state legislation and are judged the same way, except budget items that fund
  AI work at one named campus, which are out of scope (see the borderline table)."""


def triage_md(paths: Paths, vocab: Vocab) -> str:
    scope = scope_section(paths.root / "docs" / "methodology.md")
    return f"""\
# Triage instructions

You are sorting candidate bills found by an AI keyword search into three groups,
using only the bill's metadata. A later stage reads the bill text for every bill you
mark `in` or `needs_text`, so a wrong `out` loses the bill for good while a wrong
`needs_text` only costs one text download.

## Input and output

- Input: one batch file, `.cache/classify/triage/batch-NNN.jsonl`. Each line is one
  bill: `key`, `jurisdiction`, `session`, `identifier`, `title`, `description`,
  `bill_type`, `status`, `matched_by` (`title` when the title or description matched an
  AI keyword, `fulltext` when only a full-text search found it), `trigger_strength`
  (`weak` when the only keyword was "algorithm"), `matched_keywords`,
  `fulltext_queries`, and `snippets` (short passages around each match).
- Output: `.cache/classify/triage/out/batch-NNN.jsonl` (same number), one JSON object
  per input line, in the same order, every key exactly once, nothing else in the file:

```json
{{"key": "us-tx-2025-hb-1481", "decision": "in", "reason": "Requires districts to adopt student AI-use policies."}}
```

- `decision`: `in`, `out`, or `needs_text`.
- `reason`: at most {TRIAGE_REASON_WORDS} words, saying what in the metadata decided it.
- The full schema is in `.cache/classify/triage.schema.json`.
- Before finishing, run
  `uv run statescope classify check --stage triage .cache/classify/triage/out/batch-NNN.jsonl`
  and fix every error it reports.

## How to decide

- `in`: the title or description shows the measure governs AI in K-12 or higher
  education, or places an AI-related duty on schools, students, educators, or
  institutions (for example "requires school districts to adopt artificial
  intelligence policies", "AI literacy instruction", "deepfakes of students").
- `out`: the metadata settles that it is out of scope: "AI" plainly means something
  else; or the subject is narrow and unrelated to education (election deepfakes,
  rental-pricing algorithms, insurance algorithms, a health-care AI disclosure) with no
  hint of a school, student, or education provision.
- `needs_text`: anything else, including general AI, automated-decision, chatbot,
  deepfake, or online-safety bills that could contain an education section, omnibus or
  appropriations bills, education bills whose description does not say what the AI
  provision does, and every bill you are unsure about.
- Bills with `matched_by: fulltext` were found by searches that required an education
  word in the text; mark them `out` only when the metadata clearly rules them out.

## Scope (from docs/methodology.md)

{scope}

## Decision rules

{_DECISION_RULES}
"""


def full_md(paths: Paths, vocab: Vocab) -> str:
    scope = scope_section(paths.root / "docs" / "methodology.md")
    return f"""\
# Full classification instructions

You are deciding whether each bill is in scope for the dataset and, for in-scope
bills, describing what it does with codes and verbatim quotations from its text.
Your output becomes a public data record after automatic checks, so every statement
must be supported by the text you were given.

## Input and output

- Input: one batch file, `.cache/classify/full/batch-NNN.jsonl`. Each line is one bill:
  metadata (`key`, `jurisdiction`, `session`, `identifier`, `title`, `description`,
  `bill_type`, `status`, `sponsors`, `history`, `versions`), the triage decision, and
  `text`: the latest version's `type`, `date`, and `url`; its `status` (`ok` or
  `text_unavailable`); `chars` (length of the whole text); `complete` (true when the
  excerpts are the whole text); `truncated` (true when some passages that mention AI
  did not fit); and `excerpts`, the passages chosen (the opening of the bill, then
  passages that mention AI, preferring those that also mention schools, students,
  educators, or institutions). Each excerpt has the nearest section `heading` and PDF
  `page` found above it, which help with locators. Excerpts are copied from the stored
  text with whitespace collapsed; text between excerpts was left out.
- Output: `.cache/classify/full/out/batch-NNN.jsonl` (same number), one JSON object per
  input line, in the same order, every key exactly once, nothing else in the file.
- The schema is in `.cache/classify/full.schema.json`.
- Before finishing, run
  `uv run statescope classify check --stage full .cache/classify/full/out/batch-NNN.jsonl`
  and fix every error it reports. Quotes that are not found in the stored text are
  errors.

### Fields

| field | rule |
|---|---|
| `key` | copied from the input |
| `in_scope` | `true` or `false`, by the scope definition below |
| `confidence` | `high` when the text settles it; `medium` when it depends on a judgment call in the borderline table; `low` when the decisive text may be outside the excerpts or the text is unavailable |
| `education_levels` | in scope only: `k12`, `postsecondary`, or both, by who the provisions apply to |
| `topics` | in scope only: one or more topic codes from the table below |
| `summary` | in scope only: plain language, neutral, 1-{SUMMARY_MAX_SENTENCES} sentences, describing what the bill does (present tense: "Requires ...", "Creates ..."). No characterization ({", ".join(LOADED_WORDS[:8])}, and the like), no motives, no predictions, no status. |
| `provisions` | in scope only: one entry per distinct clause that matters for AI in education (usually 1-4). Each has `type` (a provision code from the table below), `description` (one plain sentence), `locator` (where it is, as the bill writes it: "Sec. 2(b)", "Section 1, new 22.0965(c)", "[Page 3]" when the bill has no section numbers; copy only numbers you can see in the excerpt or its `heading`, and when none is visible describe the place in words, e.g. "Part on AI literacy, page 4", never a reconstructed number like "Sec. 103(b)(10)"), `quote`, and optionally `education_levels` when it applies to only one level. Use `[]` when no provision type fits. |
| `quote` | at most {QUOTE_MAX} characters copied **exactly** from one excerpt: one contiguous passage, no ellipses, no brackets, no added or changed words, no fixed typos. Spacing is not compared: write line breaks as spaces, and repair spaces the PDF extraction put inside words ("ARTIFICI AL" may be written "ARTIFICIAL"). Leave out stray page markers such as "[Page 3]" by quoting a passage that does not cross one. |
| `reasons` | at most {FULL_REASON_WORDS} words: which section or clause decided the scope call, citing it ("Sec. 3 requires each school district to ..."). Required for out-of-scope bills too. |

For an out-of-scope bill, write only `key`, `in_scope: false`, `confidence`, and
`reasons`.

Example (one line in the file):

```json
{{"key": "us-il-2025-2026-hb-1859", "in_scope": true, "confidence": "high", "education_levels": ["postsecondary"], "topics": ["instructional-use", "employment-evaluation"], "summary": "Bars community college courses from using artificial intelligence as the sole source of instruction in place of a qualified faculty member, while allowing faculty to use AI to support instruction.", "provisions": [{{"type": "prohibits-ai-replacing-educators", "description": "Courses may not use AI as the sole source of instruction in place of a faculty member.", "locator": "Sec. 3-29.28(b)", "quote": "A course may not, in lieu of a faculty member, use artificial intelligence as the sole source of instruction for students."}}], "reasons": "Sec. 3-29.28(b) bars community college courses from using AI as the sole source of instruction."}}
```

## Scope (from docs/methodology.md)

{scope}

## Decision rules

{_DECISION_RULES}
- Judge every version you are given. The excerpts come from the latest version; when
  an earlier version was also fetched, its excerpts are under `text.earlier`. If an
  earlier version met the definition and the AI provisions were amended out, the bill
  is in scope: quote the earlier version (give its version type in the locator, e.g.
  "Introduced, Sec. 2"), and end the summary with a sentence saying the AI provisions
  were removed by amendment.
- When `text.status` is `text_unavailable`, decide from the metadata, set
  `confidence` to `low`, and give no provisions (there is nothing to quote).
- When the excerpts are truncated and the decision may depend on text you cannot
  see, decide on what you have, set `confidence` to `low`, and say so in `reasons`.

## Topic codes

{_topics_table(vocab)}

## Provision codes

A provision's code must fit what that clause does; the record's `education_levels`
must include any level a provision lists.

{_provisions_table(vocab)}
"""


def write_instructions(paths: Paths, out_dir: Path) -> dict[str, Path]:
    vocab = Vocab.load(paths.vocab)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "TRIAGE.md": triage_md(paths, vocab),
        "FULL.md": full_md(paths, vocab),
        "triage.schema.json": json.dumps(triage_schema(), indent=1) + "\n",
        "full.schema.json": json.dumps(full_schema(vocab), indent=1) + "\n",
    }
    rel = os.path.relpath(out_dir, paths.root)
    if rel != ".cache/classify" and not rel.startswith(".."):
        files = {k: v.replace(".cache/classify", rel) for k, v in files.items()}
    elif rel.startswith(".."):
        files = {k: v.replace(".cache/classify", str(out_dir)) for k, v in files.items()}
    written = {}
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written[name] = path
    return written
