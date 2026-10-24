from pathlib import Path

from edictai.classify.score import Counts, format_report, load_eval, match, predicted_in, score

REPO = Path(__file__).resolve().parents[2]

ENTRIES = [
    {
        "id": "us-tx-2025-2026-hb-1",
        "jurisdiction": "us-tx",
        "session": "2025-2026",
        "identifier": "HB 1",
        "label": "in_scope",
        "difficulty": "clear",
        "education_levels": ["k12"],
        "rationale": "r1",
    },
    {
        "id": "us-co-2024-sb-24-205",
        "jurisdiction": "us-co",
        "session": "2024",
        "identifier": "SB 24-205",
        "label": "in_scope",
        "difficulty": "borderline",
        "education_levels": ["k12", "postsecondary"],
        "rationale": "r2",
    },
    {
        "id": "us-fl-2026-sb-1680",
        "jurisdiction": "us-fl",
        "session": "2026",
        "identifier": "SB 1680",
        "label": "out_of_scope",
        "difficulty": "clear",
        "rationale": "r3",
    },
    {
        "id": "us-hi-2023-hcr-143",
        "jurisdiction": "us-hi",
        "session": "2023",
        "identifier": "HCR 143",
        "label": "out_of_scope",
        "difficulty": "clear",
        "rationale": "r4",
    },
]


def test_counts():
    c = Counts()
    for truth, pred in [(True, True), (True, False), (False, True), (False, False), (True, True)]:
        c.add(truth, pred)
    assert (c.tp, c.fn, c.fp, c.tn) == (2, 1, 1, 1)
    assert c.precision == 2 / 3 and c.recall == 2 / 3
    assert abs(c.f1 - 2 / 3) < 1e-9
    assert Counts().precision is None and Counts().f1 is None


def test_predicted_in():
    assert predicted_in({"in_scope": True})
    assert predicted_in({"decision": "needs_text"})
    assert not predicted_in({"decision": "needs_text"}, needs_text_as_in=False)
    assert not predicted_in({"decision": "out"})


def test_match_by_id_session_and_number():
    bills = {
        "us-tx-2025-hb-1": {"jurisdiction": "us-tx", "session": "2025", "identifier": "HB 1"},
        "us-co-2024-sb-205": {"jurisdiction": "us-co", "session": "2024", "identifier": "SB 205"},
        # Same number, different session: must not match the 2026 entry.
        "us-fl-2024-sb-1680": {"jurisdiction": "us-fl", "session": "2024", "identifier": "SB 1680"},
    }
    judgments = [
        {"key": "us-hi-2023-hcr-143", "decision": "out"},
        {"key": "us-tx-2025-hb-1", "in_scope": True},
        {"key": "us-co-2024-sb-205", "in_scope": True},
        {"key": "us-fl-2024-sb-1680", "in_scope": False},
    ]
    matched, missing, unmatched = match(ENTRIES, judgments, bills)
    how = {m.entry["id"]: m.how for m in matched}
    assert how == {
        "us-hi-2023-hcr-143": "id",
        "us-tx-2025-2026-hb-1": "number",
        "us-co-2024-sb-24-205": "session+number",
    }
    assert [e["id"] for e in missing] == ["us-fl-2026-sb-1680"]
    assert unmatched == ["us-fl-2024-sb-1680"]


def test_score_breakdowns_and_confusion():
    judgments = [
        {
            "key": "us-tx-2025-2026-hb-1",
            "in_scope": True,
            "education_levels": ["k12", "postsecondary"],
        },
        {"key": "us-co-2024-sb-24-205", "in_scope": False, "reasons": "consumer bill"},
        {"key": "us-fl-2026-sb-1680", "in_scope": True, "reasons": "thought it was AI"},
    ]
    s = score(ENTRIES, judgments)
    assert (s.overall.tp, s.overall.fp, s.overall.fn, s.overall.tn) == (1, 1, 1, 0)
    assert s.by_difficulty["borderline"].fn == 1
    assert s.by_difficulty["clear"].tp == 1 and s.by_difficulty["clear"].fp == 1
    assert s.by_level["k12"].recall == 0.5 and s.by_level["postsecondary"].recall == 0.0
    assert s.level_agreement["postsecondary"].fp == 1
    assert [r["id"] for r in s.false_positives] == ["us-fl-2026-sb-1680"]
    assert s.false_negatives[0]["judgment"] == "consumer bill"
    assert [e["id"] for e in s.missing] == ["us-hi-2023-hcr-143"]
    report = format_report(s)
    assert "False positives" in report and "us-co-2024-sb-24-205 [borderline]" in report

    with_missing = score(ENTRIES, judgments, missing_as_out=True)
    assert with_missing.overall.tn == 1 and with_missing.overall.n == 4


def test_real_eval_file_loads():
    entries = load_eval(REPO / "pipeline" / "eval" / "classifier_eval.yaml")
    assert len(entries) > 100
    assert {e["label"] for e in entries} == {"in_scope", "out_of_scope"}
