"""Instructions and reply schema for `watch-apply`'s review of a changed page.

The scope comes from docs/methodology.md and the codes from data/vocab, as in
the classifier's instructions, so the review cannot drift from either.
"""

from __future__ import annotations

from typing import Any

from ..classify.instructions import (
    LOADED_WORDS,
    QUOTE_MAX,
    SUMMARY_MAX_SENTENCES,
    _active,
    _provisions_table,
    _topics_table,
)
from ..vocab import Vocab

SYSTEM = """You keep the Edictai dataset of AI-in-education policy current. A page the dataset
watches has changed. You are given the page's text before and after (or the diffs logged
since the last review), the document records that cite the page, the other documents its
issuer already has in the dataset, and the text of documents the page links to. Decide
whether the change matters to the dataset, and if it does, say exactly how the records
should change.

## Verdict

- `noise`: nothing a record states is affected. Navigation, news feeds, event listings,
  dates a page was last modified, reworded boilerplate, a moved paragraph. Most changes
  are noise; say so when they are.
- `update`: a document the dataset records was revised, replaced, withdrawn, or newly
  issued, or the page now states something a record gets wrong or leaves out.

## Updating a record (`updates`)

Only the records listed under "Records" may be updated, by `id`. Give only the fields that
change; a field left out keeps its value.

- `note`: one sentence for the record's revision history saying what changed on the page,
  e.g. "Page now describes the Oklahoma Responsible Technology in Schools Act and its
  2027-28 district policy deadline."
- `summary`: the whole new summary, when the current one is wrong or out of date. Same
  style as the existing summaries: plain language, neutral, 1-{max_sentences} sentences, what
  the document does. No characterization ({loaded}, and the like), no motives, no
  predictions.
- `status`: `superseded` (with `superseded_by` naming the record that replaces it, when
  the dataset has it or you add it under `new_documents`) or `withdrawn` only when the
  issuer says so or the document is gone from the page with a stated replacement.
- `updated`, `version`, `published`: as the issuer gives them (`YYYY`, `YYYY-MM`, or
  `YYYY-MM-DD`; version as printed, e.g. "3.0"). Never estimate.
- `topics`: the complete new list, when topics should be added or removed.
- `add_provisions`: provisions the current text has that the record lacks. Do not repeat
  ones the record has.
- `evidence`: at least one verbatim quote, from the supplied texts, that shows the change.

## Adding a document (`new_documents`)

Add a document only when a supplied text is an in-scope policy document (see Scope) that
the dataset does not have: check the issuer's documents, their titles and source URLs,
before adding. Its `source_url` must be one of the supplied texts' URLs, and every field
must be supported by that text. `slug` is short, lower-case, hyphenated, and starts with
the issuer's short name (e.g. `osde-ai-guidance`). `related` may name records from the
lists given.

## Quotes

Every `quote` (in `evidence` and in provisions) is copied character for character from the
text of the `source_url` it names, at most {quote_max} characters. A change backed by a
quote that is not in that text is thrown out. Prefer the sentence that states the fact.

## Review

Use `review` for anything a person should look at that you could not settle from the
texts: a linked document that could not be read, a change you are unsure is real, a
record that may now be wrong in a way the texts do not let you fix.

## Scope

{scope}

## Document classifications

{classifications}

Statuses: {statuses}.

## Topics

{topics}

## Provision types

{provisions}
"""


def system_prompt(vocab: Vocab, scope: str) -> str:
    classes = vocab.labels("document_classifications")
    return SYSTEM.format(
        max_sentences=SUMMARY_MAX_SENTENCES,
        loaded=", ".join(LOADED_WORDS[:8]),
        quote_max=QUOTE_MAX,
        scope=scope,
        classifications="\n".join(f"- `{k}`: {v}" for k, v in classes.items()),
        statuses=", ".join(f"`{s}`" for s in sorted(vocab.codes("document_statuses"))),
        topics=_topics_table(vocab),
        provisions=_provisions_table(vocab),
    )


def reply_schema(vocab: Vocab) -> dict[str, Any]:
    levels = sorted(vocab.codes("education_levels"))
    topics = {"type": "array", "items": {"enum": _active(vocab.topics)}}
    evidence = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["source_url", "quote"],
            "additionalProperties": False,
            "properties": {"source_url": {"type": "string"}, "quote": {"type": "string"}},
        },
    }
    provision = {
        "type": "object",
        "required": ["type", "description", "source_url", "locator", "quote"],
        "additionalProperties": False,
        "properties": {
            "type": {"enum": _active(vocab.provision_types)},
            "description": {"type": "string"},
            "source_url": {"type": "string"},
            "locator": {"type": "string"},
            "quote": {"type": "string"},
            "education_levels": {"type": "array", "items": {"enum": levels}},
        },
    }
    status = {"enum": sorted(vocab.codes("document_statuses"))}
    return {
        "type": "object",
        "required": ["verdict", "what_changed", "updates", "new_documents", "review"],
        "additionalProperties": False,
        "properties": {
            "verdict": {"enum": ["noise", "update"]},
            "what_changed": {"type": "string"},
            "updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "note", "evidence"],
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "note": {"type": "string"},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "status": status,
                        "superseded_by": {"type": "string"},
                        "published": {"type": "string"},
                        "updated": {"type": "string"},
                        "version": {"type": "string"},
                        "topics": topics,
                        "add_provisions": {"type": "array", "items": provision},
                        "evidence": evidence,
                    },
                },
            },
            "new_documents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "slug",
                        "title",
                        "classification",
                        "status",
                        "education_levels",
                        "topics",
                        "summary",
                        "source_url",
                        "source_title",
                        "provisions",
                        "evidence",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "slug": {"type": "string"},
                        "title": {"type": "string"},
                        "classification": {"enum": sorted(vocab.codes("document_classifications"))},
                        "status": status,
                        "education_levels": {"type": "array", "items": {"enum": levels}},
                        "topics": topics,
                        "summary": {"type": "string"},
                        "published": {"type": "string"},
                        "updated": {"type": "string"},
                        "version": {"type": "string"},
                        "source_url": {"type": "string"},
                        "source_title": {"type": "string"},
                        "provisions": {"type": "array", "items": provision},
                        "related": {"type": "array", "items": {"type": "string"}},
                        "evidence": evidence,
                    },
                },
            },
            "review": {"type": "array", "items": {"type": "string"}},
        },
    }
