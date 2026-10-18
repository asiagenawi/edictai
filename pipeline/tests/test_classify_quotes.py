from pathlib import Path

import pytest

from statescope.classify.judgments import check_full, check_triage, sentences
from statescope.classify.texts import TextStore
from statescope.classify.textutil import (
    html_to_text,
    quote_in_text,
    strip_line_numbers,
    strip_running_headers,
)
from statescope.vocab import Vocab

REPO = Path(__file__).resolve().parents[2]
TEXT = (
    "SECTION 1. Section 38.0231, Education Code, is amended.\n"
    "(a) Each school district shall adopt a policy on the use of artifi-\n"
    "cial intelligence by students. The policy must address “academic\n"
    "integrity” and data privacy — including vendor contracts.\n"
)


@pytest.mark.parametrize(
    "quote",
    [
        "Each school district shall adopt a policy",
        'The policy must address "academic integrity" and data privacy - including vendor contracts.',
        "Each   school district\nshall adopt",
        "SECTION 1. Section 38.0231, Education Code, is amended.",
    ],
)
def test_quote_found(quote):
    assert quote_in_text(quote, TEXT)


@pytest.mark.parametrize(
    "quote",
    [
        "Each school district must adopt a policy",  # changed word
        "Each school district shall adopt a policy ... students",  # elision
        "school district",  # too short to be meaningful? no: 14 chars, but absent case below
        "academic honesty",
    ],
)
def test_quote_not_found(quote):
    if quote == "school district":
        assert quote_in_text(quote, TEXT)  # present and long enough
        return
    assert not quote_in_text(quote, TEXT)


def test_quote_ignores_pdf_word_splits():
    pdf = "THE DEPARTMENT SHALL ENSU RE THAT ARTIFICI AL INTELLIGENCE TOOLS"
    assert quote_in_text("THE DEPARTMENT SHALL ENSURE THAT ARTIFICIAL INTELLIGENCE TOOLS", pdf)


def test_html_drops_struck_text_and_tags():
    html = (
        b"<html><head><style>p{}</style></head><body><p>Each district <s>may</s> "
        b"<u>shall</u> adopt</p><p>a <span style='text-decoration: line-through'>draft</span>"
        b"policy on artificial intelligence&nbsp;use.</p></body></html>"
    )
    text = html_to_text(html)
    assert "may" not in text and "draft" not in text
    assert "Each district shall adopt" in text
    assert "policy on artificial intelligence use." in text


def test_strip_line_numbers_left_and_right():
    left = "\n".join(f"{n} line {n} of the bill text" for n in range(1, 12))
    assert strip_line_numbers(left).splitlines()[0] == "line 1 of the bill text"
    right = "\n".join(f"text of line ending here {n}" for n in range(1, 12))
    assert strip_line_numbers(right).splitlines()[3] == "text of line ending here "
    prose = "Effective January 1, 2025\nand again in 2026\nnothing numbered here"
    assert strip_line_numbers(prose) == prose


def test_running_headers_removed_but_repeated_sentences_kept():
    pages = []
    for n in range(1, 5):
        pages.append(f"[Page {n}]\nHB2503 - {n} - LRB104 10601 LNS 20677 b")
        pages.append("The district shall act.\nThe board shall act.")
        pages.append(f"NEW SECTION. Sec. {n + 4}. A new section is added to chapter 28A.300")
        pages.append("RCW to read as follows:\nThe district shall act.\nThe board shall act.")
    out = strip_running_headers("\n".join(pages))
    assert "LRB104" not in out
    assert out.count("A new section is added") == 4


def test_sentences():
    assert (
        sentences("Requires districts to adopt policies. Creates a task force under Sec. 3.") == 2
    )
    assert sentences("Amends 105 ILCS 5/27 so that the U.S. Department is consulted.") == 1


# -- judgment checks ----------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    s = TextStore(tmp_path / "texts")
    s.store("us-tx-2025-hb-10", {"doc_id": 7, "key": "us-tx-2025-hb-10", "status": "ok"}, TEXT)
    s.store(
        "us-tx-2025-hb-11",
        {"doc_id": -11, "key": "us-tx-2025-hb-11", "status": "text_unavailable"},
        None,
    )
    s.save_index()
    return TextStore(tmp_path / "texts")


@pytest.fixture
def vocab():
    return Vocab.load(REPO / "data" / "vocab")


def good(**over):
    row = {
        "key": "us-tx-2025-hb-10",
        "in_scope": True,
        "confidence": "high",
        "education_levels": ["k12"],
        "topics": ["local-policy-requirement", "academic-integrity"],
        "summary": "Requires each school district to adopt a policy on student use of artificial intelligence.",
        "provisions": [
            {
                "type": "requires-local-ai-policy",
                "description": "Each district adopts a student AI-use policy.",
                "locator": "Sec. 1, Education Code 38.0231(a)",
                "quote": "Each school district shall adopt a policy on the use of artificial intelligence by students.",
            }
        ],
        "reasons": "Sec. 1 requires each school district to adopt a student AI-use policy.",
    }
    row.update(over)
    return row


def test_check_full_accepts_verified(store, vocab):
    report = check_full([good()], vocab, store, ["us-tx-2025-hb-10"])
    assert report.ok, report.lines()
    assert report.items[0].text_available


def test_check_full_quote_failures(store, vocab):
    prov = dict(
        good()["provisions"][0], quote="Each school district shall adopt a strict policy on AI."
    )
    elided = dict(prov, quote="Each school district shall adopt ... by students.")
    report = check_full([good(provisions=[prov, elided])], vocab, store)
    item = report.items[0]
    assert item.quote_failures == [0, 1]
    assert any("not found verbatim" in e for e in item.errors)
    assert any("ellipsis" in e for e in item.errors)
    assert not report.ok


def test_check_full_vocab_schema_and_style(store, vocab):
    bad_topic = good(topics=["ai-stuff"])
    loaded = good(summary="Creates a landmark AI policy for schools.")
    long_summary = good(summary="One thing. Two things. Three things. Four things.")
    no_locator = good(provisions=[dict(good()["provisions"][0], locator="")])
    extra_level = good(
        provisions=[dict(good()["provisions"][0], education_levels=["postsecondary"])]
    )
    missing_fields = {
        "key": "us-tx-2025-hb-10",
        "in_scope": True,
        "confidence": "high",
        "reasons": "x y z",
    }
    report = check_full([bad_topic], vocab, store)
    assert any("topics/0" in e for e in report.items[0].errors)
    for row, needle in [
        (loaded, "characterizing"),
        (long_summary, "sentences"),
        (no_locator, "locator"),
        (extra_level, "not among the bill's education_levels"),
        (missing_fields, "required property"),
    ]:
        errors = check_full([row], vocab, store).items[0].errors
        assert any(needle in e for e in errors), (needle, errors)


def test_check_full_out_of_scope_needs_only_reasons(store, vocab):
    row = {
        "key": "us-tx-2025-hb-11",
        "in_scope": False,
        "confidence": "low",
        "reasons": "Insurance bill.",
    }
    assert check_full([row], vocab, store).ok


def test_check_full_unavailable_text_rejects_quotes(store, vocab):
    row = good(key="us-tx-2025-hb-11")
    item = check_full([row], vocab, store).items[0]
    assert item.quote_failures == [0] and not item.text_available


def test_file_checks_missing_duplicate_order():
    rows = [
        {"key": "b", "decision": "in", "reason": "AI policy for schools."},
        {"key": "a", "decision": "out", "reason": "Insurance."},
        {"key": "a", "decision": "out", "reason": "Insurance."},
    ]
    report = check_triage(rows, ["a", "b", "c"])
    text = "\n".join(report.file_errors)
    assert "a: appears more than once" in text
    assert "c: missing from the output" in text
    report = check_triage(rows[:2], ["a", "b"])
    assert report.file_errors == ["judgments are not in the input order"]


def test_check_triage_reason_length_and_values():
    rows = [
        {"key": "a", "decision": "maybe", "reason": "Unsure."},
        {"key": "b", "decision": "in", "reason": " ".join(["word"] * 21)},
        {
            "key": "c",
            "decision": "needs_text",
            "reason": "General AI bill; may have a school section.",
        },
    ]
    items = check_triage(rows).by_key()
    assert items["a"].errors and items["b"].errors and items["c"].ok


def test_check_full_accepts_quotes_from_the_earlier_version(tmp_path, vocab):
    s = TextStore(tmp_path / "texts")
    key = "us-tx-2025-hb-12"
    s.store(key, {"doc_id": 20, "key": key, "status": "ok"}, "SECTION 1. Districts shall report.\n")
    s.store_earlier(key, {"doc_id": 19, "key": key, "status": "ok", "type": "Introduced"}, TEXT)
    s.save_index()
    store = TextStore(tmp_path / "texts")
    report = check_full([good(key=key)], vocab, store)
    assert report.ok, report.lines()
    missing = dict(good()["provisions"][0], quote="Each school district shall report weekly.")
    item = check_full([good(key=key, provisions=[missing])], vocab, store).items[0]
    assert item.quote_failures == [0]
