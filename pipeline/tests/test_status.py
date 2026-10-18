from datetime import date

import pytest

from statescope.status import derive_status

TODAY = date(2026, 10, 17)


def act(when, *classes, actor=None):
    a = {"date": when, "description": "x", "classification": list(classes)}
    if actor:
        a["actor"] = actor
    return a


def status(actions, **kw):
    kw.setdefault("today", TODAY)
    return derive_status(actions, **kw).status


INTRO = act("2025-01-10", "introduction", actor="lower")
REFER = act("2025-01-12", "referral-committee", actor="lower")
PASS_LOWER = act("2025-02-01", "passage", actor="lower")
PASS_UPPER = act("2025-03-01", "passage", actor="upper")


def test_introduced():
    assert status([INTRO]) == "introduced"


def test_in_committee():
    assert status([INTRO, REFER]) == "in-committee"


def test_passed_one_chamber():
    assert status([INTRO, REFER, PASS_LOWER]) == "passed-chamber"


def test_second_chamber_committee_keeps_passed_chamber():
    later_referral = act("2025-02-10", "referral-committee", actor="upper")
    assert status([INTRO, PASS_LOWER, later_referral]) == "passed-chamber"


def test_passed_both_chambers():
    assert status([INTRO, PASS_LOWER, PASS_UPPER]) == "passed-legislature"


def test_same_chamber_twice_is_not_both():
    concur = act("2025-03-05", "passage", actor="lower")
    assert status([INTRO, PASS_LOWER, concur]) == "passed-chamber"


def test_unicameral_single_passage_completes():
    assert status(
        [INTRO, act("2025-02-01", "passage", actor="legislature")], jurisdiction="us-ne"
    ) == ("passed-legislature")
    assert (
        status([INTRO, act("2025-02-01", "passage")], jurisdiction="us-ne") == "passed-legislature"
    )


def test_executive_receipt_implies_passed_legislature():
    assert (
        status([INTRO, act("2025-04-01", "executive-receipt", actor="executive")])
        == "passed-legislature"
    )


def test_signed():
    result = derive_status(
        [
            INTRO,
            PASS_LOWER,
            PASS_UPPER,
            act("2025-04-01", "executive-signature", actor="executive"),
        ],
        today=TODAY,
    )
    assert result.status == "enacted"
    assert result.enacted == "2025-04-01"
    assert result.status_date == "2025-04-01"
    assert result.introduced == "2025-01-10"


def test_became_law_without_signature():
    assert status([INTRO, PASS_LOWER, PASS_UPPER, act("2025-04-20", "became-law")]) == "enacted"


def test_enactment_is_terminal():
    later = act("2025-05-01", "failure", actor="lower")
    assert (
        status([INTRO, PASS_LOWER, PASS_UPPER, act("2025-04-01", "executive-signature"), later])
        == "enacted"
    )


def test_vetoed():
    assert status([INTRO, PASS_LOWER, PASS_UPPER, act("2025-04-01", "executive-veto")]) == "vetoed"


def test_veto_stays_vetoed_after_session_end():
    actions = [INTRO, PASS_LOWER, PASS_UPPER, act("2025-04-01", "executive-veto")]
    assert status(actions, session_end="2025-06-01") == "vetoed"


def test_override_needs_both_chambers():
    veto = act("2025-04-01", "executive-veto")
    one = act("2025-04-10", "veto-override-passage", actor="lower")
    two = act("2025-04-12", "veto-override-passage", actor="upper")
    assert status([INTRO, PASS_LOWER, PASS_UPPER, veto, one]) == "vetoed"
    assert status([INTRO, PASS_LOWER, PASS_UPPER, veto, one, two]) == "enacted"


def test_override_by_legislature_completes():
    veto = act("2025-04-01", "executive-veto")
    override = act("2025-04-10", "veto-override-passage", actor="legislature")
    assert status([INTRO, PASS_LOWER, PASS_UPPER, veto, override]) == "enacted"


def test_line_item_veto_with_signature_is_enacted():
    both = act("2025-04-01", "executive-signature", "executive-veto")
    assert status([INTRO, PASS_LOWER, PASS_UPPER, both]) == "enacted"


@pytest.mark.parametrize(
    "kind", ["failure", "committee-failure", "withdrawal", "died-at-session-end"]
)
def test_failures(kind):
    assert status([INTRO, REFER, act("2025-02-01", kind)]) == "failed"


def test_revived_after_committee_failure():
    fail = act("2025-02-01", "committee-failure")
    revived = act("2025-02-15", "committee-passage")
    assert status([INTRO, REFER, fail, revived]) == "in-committee"


def test_session_end_fails_pending_bill():
    result = derive_status([INTRO, REFER], session_end="2025-06-01", today=TODAY)
    assert result.status == "failed"
    assert result.status_date is None


def test_session_end_fails_bill_that_passed_one_chamber():
    assert status([INTRO, PASS_LOWER], session_end="2025-06-01") == "failed"


def test_session_end_keeps_bill_awaiting_governor():
    assert status([INTRO, PASS_LOWER, PASS_UPPER], session_end="2025-06-01") == "passed-legislature"


def test_session_not_over():
    assert status([INTRO, REFER], session_end="2027-01-03") == "in-committee"
    assert status([INTRO, REFER], session_end=None) == "in-committee"


def test_session_ending_today_is_not_over():
    assert status([INTRO], session_end=TODAY.isoformat()) == "introduced"


def test_adopted_simple_resolution_does_not_fail():
    assert status([INTRO, PASS_LOWER], classification="resolution", session_end="2025-06-01") == (
        "passed-chamber"
    )


def test_concurrent_resolution_complete_after_both_chambers():
    actions = [INTRO, PASS_LOWER, PASS_UPPER]
    assert status(actions, classification="concurrent-resolution", session_end="2025-06-01") == (
        "passed-legislature"
    )


def test_unordered_input_is_sorted():
    assert status([PASS_UPPER, INTRO, PASS_LOWER]) == "passed-legislature"


def test_classification_as_bare_string():
    assert status(
        [{"date": "2025-01-01", "description": "x", "classification": "referral-committee"}]
    ) == ("in-committee")


def test_last_action_and_status_date():
    result = derive_status([INTRO, REFER, act("2025-01-20", "committee-hearing")], today=TODAY)
    assert result.last_action == "2025-01-20"
    assert result.status_date == "2025-01-12"


def test_no_actions():
    result = derive_status([], today=TODAY)
    assert result.status == "introduced"
    assert result.introduced is None
