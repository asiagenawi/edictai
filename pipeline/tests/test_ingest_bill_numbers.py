import pytest

from statescope.ingest.bill_numbers import legiscan_key, match_key, normalize


@pytest.mark.parametrize(
    ("jurisdiction", "number", "expected"),
    [
        ("us", "HB9211", "H.R. 9211"),
        ("us", "SB2740", "S. 2740"),
        ("us", "HR5", "H.Res. 5"),
        ("us", "HJR3", "H.J.Res. 3"),
        ("us", "SCR12", "S.Con.Res. 12"),
        ("us", "H.R. 9211", "H.R. 9211"),  # already official
        ("us-ut", "HB0168", "HB 168"),
        ("us-fl", "S1194", "SB 1194"),
        ("us-fl", "H1503", "HB 1503"),
        ("us-va", "HJR32", "HJ 32"),
        ("us-vt", "H650", "H 650"),  # Vermont's own prefix stays
        ("us-tx", "HB1481", "HB 1481"),
        ("us-dc", "B26-0491", "B26-0491"),
    ],
)
def test_normalize(jurisdiction, number, expected):
    assert normalize(jurisdiction, number) == expected


@pytest.mark.parametrize(
    ("jurisdiction", "record", "legiscan"),
    [
        ("us", "H.R. 610", "HB610"),
        ("us", "S. 2740", "SB2740"),
        ("us-ut", "H.B. 218", "HB0218"),
        ("us-fl", "HB 1361", "H1361"),
        ("us-wa", "E2SSB 5838", "SB5838"),
        ("us-ny", "A 6874-A", "A6874"),
        ("us-co", "SB 25-288", "SB288"),
        ("us-vt", "H.650", "H650"),
        ("us-va", "HJ 32", "HJR32"),
    ],
)
def test_record_identifier_matches_legiscan_number(jurisdiction, record, legiscan):
    assert match_key(jurisdiction, record) == legiscan_key(jurisdiction, legiscan)


def test_resolution_is_not_a_bill():
    assert legiscan_key("us", "HR610") != match_key("us", "H.R. 610")


def test_unparseable():
    assert match_key("us-tx", "Budget") is None
