import pytest

from edictai.ingest.session_ids import assign_identifiers, congress_number, special_ordinal


@pytest.mark.parametrize(
    ("year", "number"), [(2023, 118), (2024, 118), (2025, 119), (2026, 119), (1789, 1)]
)
def test_congress_number(year, number):
    assert congress_number(year) == number


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (("1st Special Session",), 1),
        (("Special Session", "2024 Fourth Special Session"), 4),
        (("2025 Second Extraordinary Session",), 2),
        (("69th Legislative Assembly Special Session 2",), 2),
        (("2023 Special Session #1",), 1),
        (("Special Session", "2023 1st House Extraordinary Session"), None),
        (("Special Session", None, ""), None),
    ],
)
def test_special_ordinal(names, expected):
    assert special_ordinal(*names) == expected


def s(session_id, state, start, end=None, special=0, tag="Regular Session", name=None):
    return {
        "session_id": session_id,
        "state": state,
        "year_start": start,
        "year_end": end or start,
        "special": special,
        "session_tag": tag,
        "session_name": name or tag,
    }


def test_assign_identifiers():
    ids = assign_identifiers(
        [
            s(1, "TX", 2025),
            s(2, "CA", 2025, 2026),
            s(3, "US", 2025, 2026),
            s(4, "US", 2023, 2024),
            s(5, "TX", 2025, special=1, tag="2nd Special Session"),
            s(6, "TX", 2025, special=1, tag="1st Special Session"),
            # No ordinal in the name: numbered after the ones that have one.
            s(7, "UT", 2023, special=1, tag="Special Session", name="2023 1st Special Session"),
            s(8, "UT", 2023, special=1, tag="Special Session", name="House Extraordinary"),
            s(9, "CA", 2025, 2026, special=1, tag="1st Extraordinary Session"),
        ]
    )
    assert ids == {
        1: "2025",
        2: "2025-2026",
        3: "119",
        4: "118",
        5: "2025-s2",
        6: "2025-s1",
        7: "2023-s1",
        8: "2023-s2",
        9: "2025-s1",
    }


def test_duplicate_ordinals_fall_back_to_sequence():
    ids = assign_identifiers(
        [
            s(10, "LA", 2024, special=1, tag="1st Special Session"),
            s(11, "LA", 2024, special=1, tag="1st Special Session"),
        ]
    )
    assert ids == {10: "2024-s1", 11: "2024-s2"}
