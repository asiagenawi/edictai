import json
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from edictai.federal.actions import RULES, actor, classify, normalize_action
from edictai.federal.congress import (
    CongressClient,
    CongressError,
    congress_gov_url,
    normalize_bill,
    ordinal,
    strip_html,
)
from edictai.federal.existing import build_index, fr_document_number
from edictai.federal.federal_register import (
    FederalRegisterClient,
    agency_documents,
    executive_orders,
    normalize_document,
    presidential_memoranda,
)
from edictai.federal.run import find_federal_candidates
from edictai.ingest.http import HTTPError, RateLimiter

KEY = "test-key-not-real"
NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
VOCAB = Path(__file__).resolve().parents[2] / "data" / "vocab" / "enums.yaml"


def no_wait() -> RateLimiter:
    return RateLimiter(0, sleep=lambda _s: None)


# -- action mapping -----------------------------------------------------------

ACTION_TABLE = [
    ("1000", "Introduced in House", ["introduction"]),
    ("10000", "Introduced in Senate", ["introduction"]),
    (
        "H11100",
        "Referred to the House Committee on Education and the Workforce.",
        ["referral-committee"],
    ),
    ("H11000", "Referred to the Subcommittee on Early Childhood.", ["referral-committee"]),
    (
        None,
        "Read twice and referred to the Committee on Health, Education, Labor, and Pensions.",
        ["reading-2", "referral-committee"],
    ),
    (
        None,
        "Committee on Commerce, Science, and Transportation. Hearings held.",
        ["committee-hearing"],
    ),
    ("H15000-B", "Committee Consideration and Mark-up Session Held", ["other"]),
    (
        "H19000",
        "Ordered to be Reported (Amended) by the Yeas and Nays: 58 - 1.",
        ["committee-passage"],
    ),
    (
        "5000",
        "Reported (Amended) by the Committee on Armed Services. H. Rept. 118-125.",
        ["committee-passage"],
    ),
    (
        None,
        "Committee on Commerce, Science, and Transportation. Reported by Senator Cruz with an amendment in the nature of a substitute.",
        ["committee-passage"],
    ),
    (
        "H23000",
        "Forwarded by Subcommittee to Full Committee by Voice Vote .",
        ["committee-passage"],
    ),
    (
        "8000",
        "Passed/agreed to in House: On motion to suspend the rules and pass the bill Agreed to by the Yeas and Nays: (2/3 required): 409 - 2 (Roll no. 104).",
        ["passage"],
    ),
    ("H37100", "On passage Passed by the Yeas and Nays: 219 - 210 (Roll no. 328).", ["passage"]),
    (None, "Passed Senate without amendment by Unanimous Consent.", ["passage"]),
    (
        None,
        "Read the third time. Passed Senate with an amendment by Yea-Nay Vote. 86 - 11.",
        ["reading-3", "passage"],
    ),
    (
        None,
        "Submitted in the Senate, considered, and agreed to without amendment and with a preamble by Unanimous Consent.",
        ["passage"],
    ),
    (
        "H36210",
        "On motion to recommit Failed by the Yeas and Nays: 210 - 217 (Roll no. 327).",
        ["other"],
    ),
    (
        "9000",
        "Failed of passage in House: On passage Failed by the Yeas and Nays: 200 - 220.",
        ["failure"],
    ),
    ("H1L220", "Rule H. Res. 583 passed House.", ["other"]),
    ("H25200", "Conference report H. Rept. 118-301 filed.", ["conference-committee"]),
    (
        "23000",
        "Conference report agreed to in Senate: Senate agreed to conference report by Yea-Nay Vote.",
        ["conference-committee"],
    ),
    (None, "Measure laid before Senate by unanimous consent.", ["other"]),
    (None, "Message on Senate action sent to the House.", ["other"]),
    ("H38310", "Motion to reconsider laid on the table Agreed to without objection.", ["other"]),
    (
        None,
        "Amendment SA 1234 proposed by Senator Smith. Amendment SA 1234 agreed to in Senate by Unanimous Consent.",
        ["amendment-passage"],
    ),
    ("28000", "Presented to President.", ["enrollment"]),
    ("E20000", "Presented to President.", ["enrollment"]),
    ("E30000", "Signed by President.", ["executive-signature"]),
    ("36000", "Signed by President.", ["executive-signature"]),
    ("E40000", "Became Public Law No: 119-12.", ["became-law"]),
    ("36000", "Became Public Law No: 119-12.", ["became-law"]),
    ("31000", "Vetoed by President.", ["executive-veto"]),
    (None, "Passed Senate over veto by Yea-Nay Vote. 70 - 28.", ["veto-override-passage"]),
    ("H14000", "Received in the House.", ["other"]),
    (None, "Submitted in House", ["introduction"]),
    (
        "H34400",
        "The House adopted the amendments en gros as agreed to by the Committee of the Whole House.",
        ["amendment-passage"],
    ),
    (
        None,
        "On motion that the House disagree to the Senate amendment, and request a conference Agreed to.",
        ["conference-committee"],
    ),
    (
        None,
        "Placed on Senate Legislative Calendar under General Orders. Calendar No. 12.",
        ["other"],
    ),
]


@pytest.mark.parametrize(("code", "text", "expected"), ACTION_TABLE)
def test_classify(code, text, expected):
    assert classify(code, text) == expected


def test_rules_use_only_vocabulary_codes():
    import yaml

    allowed = set(yaml.safe_load(VOCAB.read_text())["action_classifications"])
    used = {c for rule in RULES for c in rule.classification} | {
        "reading-1",
        "reading-2",
        "reading-3",
    }
    assert used <= allowed
    assert {c for _code, _text, exp in ACTION_TABLE for c in exp} <= allowed


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (
            {
                "type": "IntroReferral",
                "sourceSystem": {"name": "Library of Congress"},
                "text": "Introduced in Senate",
            },
            "upper",
        ),
        (
            {
                "type": "IntroReferral",
                "sourceSystem": {"name": "House floor actions"},
                "text": "Referred to the House Committee on X.",
            },
            "lower",
        ),
        (
            {
                "type": "Committee",
                "sourceSystem": {"name": "House committee actions"},
                "text": "Referred to the Subcommittee on Y.",
            },
            "committee",
        ),
        (
            {
                "type": "IntroReferral",
                "sourceSystem": {"name": "Senate"},
                "text": "Read twice and referred to the Committee on Z.",
            },
            "upper",
        ),
        (
            {
                "type": "President",
                "sourceSystem": {"name": "Library of Congress"},
                "text": "Signed by President.",
            },
            "executive",
        ),
        (
            {
                "type": "President",
                "sourceSystem": {"name": "Library of Congress"},
                "text": "Presented to President.",
            },
            "legislature",
        ),
    ],
)
def test_actor(action, expected):
    assert actor(action, classify(action.get("actionCode"), action["text"])) == expected


def test_normalize_action_keeps_codes_and_committees():
    row = normalize_action(
        {
            "actionDate": "2025-09-09",
            "committees": [{"name": "Health, Education, Labor, and Pensions Committee"}],
            "sourceSystem": {"name": "Senate"},
            "text": "Read twice and referred to the Committee on Health, Education, Labor, and Pensions.",
            "type": "IntroReferral",
        }
    )
    assert row == {
        "date": "2025-09-09",
        "text": "Read twice and referred to the Committee on Health, Education, Labor, and Pensions.",
        "actor": "upper",
        "classification": ["reading-2", "referral-committee"],
        "action_code": None,
        "action_type": "IntroReferral",
        "source_system": "Senate",
        "committees": ["Health, Education, Labor, and Pensions Committee"],
    }


# -- Congress.gov fake API ----------------------------------------------------


def list_bill(congress, bill_type, number, title, update="2026-01-01"):
    return {
        "congress": congress,
        "type": bill_type.upper(),
        "number": str(number),
        "title": title,
        "updateDate": update,
        "updateDateIncludingText": update,
        "latestAction": {"actionDate": "2025-01-01", "text": "x"},
    }


def details(congress, bill_type, number, title):
    return {
        f"/bill/{congress}/{bill_type}/{number}": {
            "bill": {
                "congress": congress,
                "type": bill_type.upper(),
                "number": str(number),
                "title": title,
                "introducedDate": "2025-09-09",
                "originChamber": "Senate",
                "policyArea": {"name": "Education"},
                "legislationUrl": congress_gov_url(congress, bill_type, number),
                "latestAction": {"actionDate": "2025-09-09", "text": "Read twice and referred."},
                "sponsors": [
                    {
                        "fullName": "Sen. Doe, Jane [D-XX]",
                        "bioguideId": "D000001",
                        "party": "D",
                        "state": "XX",
                    }
                ],
                "updateDateIncludingText": "2026-01-01",
            }
        },
        f"/bill/{congress}/{bill_type}/{number}/actions": {
            "actions": [
                {
                    "actionDate": "2025-09-09",
                    "sourceSystem": {"name": "Senate"},
                    "text": "Read twice and referred to the Committee on Health, Education, Labor, and Pensions.",
                    "type": "IntroReferral",
                },
                {
                    "actionCode": "10000",
                    "actionDate": "2025-09-09",
                    "sourceSystem": {"name": "Library of Congress"},
                    "text": "Introduced in Senate",
                    "type": "IntroReferral",
                },
            ],
            "pagination": {"count": 2},
        },
        f"/bill/{congress}/{bill_type}/{number}/cosponsors": {
            "cosponsors": [
                {
                    "fullName": "Sen. Roe, Rick [R-YY]",
                    "bioguideId": "R000002",
                    "party": "R",
                    "state": "YY",
                    "isOriginalCosponsor": True,
                    "sponsorshipDate": "2025-09-09",
                }
            ],
            "pagination": {"count": 1},
        },
        f"/bill/{congress}/{bill_type}/{number}/text": {
            "textVersions": [
                {
                    "date": "2025-09-09T04:00:00Z",
                    "type": "Introduced in Senate",
                    "formats": [
                        {"type": "Formatted Text", "url": "https://www.congress.gov/x.htm"},
                        {"type": "PDF", "url": "https://www.congress.gov/x.pdf"},
                    ],
                }
            ],
            "pagination": {"count": 1},
        },
        f"/bill/{congress}/{bill_type}/{number}/summaries": {
            "summaries": [
                {
                    "actionDate": "2025-09-09",
                    "actionDesc": "Introduced in Senate",
                    "text": "<p>Old.</p>",
                },
                {
                    "actionDate": "2025-10-01",
                    "actionDesc": "Reported to Senate",
                    "text": "<p><strong>Act</strong></p><p>Covers artificial&nbsp;intelligence.</p>",
                },
            ],
            "pagination": {"count": 2},
        },
        f"/bill/{congress}/{bill_type}/{number}/titles": {
            "titles": [
                {"title": title, "titleType": "Display Title"},
                {"title": f"A bill about {title}.", "titleType": "Official Title as Introduced"},
            ],
            "pagination": {"count": 2},
        },
    }


class FakeCongress:
    def __init__(self, bills_by_congress, summaries_by_congress=None, extra=None):
        self.bills = bills_by_congress
        self.summaries = summaries_by_congress or {}
        self.routes = extra or {}
        self.calls = []

    def __call__(self, url, params, headers):
        assert url.startswith("https://api.congress.gov/v3/")
        assert params["api_key"] == KEY
        assert params["format"] == "json"
        path = url.removeprefix("https://api.congress.gov/v3")
        self.calls.append((path, {k: v for k, v in params.items() if k != "api_key"}))
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 250))
        parts = path.strip("/").split("/")
        if parts[0] == "bill" and len(parts) == 2:
            items = self.bills.get(int(parts[1]), [])
            return json.dumps(
                {"bills": items[offset : offset + limit], "pagination": {"count": len(items)}}
            ).encode()
        if parts[0] == "summaries":
            assert "fromDateTime" in params and "toDateTime" in params
            items = self.summaries.get(int(parts[1]), [])
            return json.dumps(
                {"summaries": items[offset : offset + limit], "pagination": {"count": len(items)}}
            ).encode()
        if path in self.routes:
            return json.dumps(self.routes[path]).encode()
        raise HTTPError(f"HTTP 404 from {url}")


def congress_client(tmp_path, fake, **kw):
    return CongressClient(
        KEY, tmp_path / "congress", transport=fake, limiter=no_wait(), now=lambda: NOW, **kw
    )


def test_client_requires_key(tmp_path):
    with pytest.raises(CongressError, match="CONGRESS_API_KEY"):
        CongressClient("", tmp_path)


def test_client_repr_and_errors_hide_key(tmp_path):
    def boom(url, params, headers):
        raise HTTPError(f"HTTP 403 from {url}?api_key={params['api_key']}")

    client = congress_client(tmp_path, boom, retries=0)
    assert KEY not in repr(client)
    with pytest.raises(CongressError) as exc:
        client.get("/bill/119", {}, max_age=None)
    assert KEY not in str(exc.value)


def test_client_retries_rate_limit(tmp_path):
    calls = []
    slept = []

    def flaky(url, params, headers):
        calls.append(url)
        if len(calls) == 1:
            raise HTTPError("HTTP 429 from x")
        return b'{"bills": [], "pagination": {"count": 0}}'

    client = congress_client(tmp_path, flaky, sleep=slept.append)
    assert client.get("/bill/119", {}, max_age=None) == {"bills": [], "pagination": {"count": 0}}
    assert len(calls) == 2 and slept == [30.0]


def test_api_error_body(tmp_path):
    client = congress_client(tmp_path, lambda u, p, h: b'{"error": {"code": "API_KEY_INVALID"}}')
    with pytest.raises(CongressError, match="API_KEY_INVALID"):
        client.get("/bill/119", {}, max_age=None)


def test_pagination_and_list_cache(tmp_path):
    bills = [list_bill(119, "hr", n, f"Bill {n}") for n in range(1, 8)]
    fake = FakeCongress({119: bills})
    client = congress_client(tmp_path, fake)
    got = list(client.pages("/bill/119", "bills", page_size=3))
    assert [b["number"] for b in got] == [str(n) for n in range(1, 8)]
    assert len(fake.calls) == 3
    assert list(client.pages("/bill/119", "bills", page_size=3)) == got
    assert len(fake.calls) == 3  # cached
    assert client.queries == 3 and client.cache_hits == 3
    for f in (tmp_path / "congress").rglob("*.json"):
        assert KEY not in f.read_text()


def test_list_cache_expires(tmp_path):
    fake = FakeCongress({119: [list_bill(119, "hr", 1, "x")]})
    now = [NOW]
    client = CongressClient(KEY, tmp_path, transport=fake, limiter=no_wait(), now=lambda: now[0])
    list(client.bills(119))
    now[0] = NOW + timedelta(days=2)
    list(client.bills(119))
    assert len(fake.calls) == 2


def test_bill_details_cached_until_update_date_changes(tmp_path):
    fake = FakeCongress({}, extra=details(119, "s", 2740, "RAISE Act of 2025"))
    client = congress_client(tmp_path, fake)
    first = client.bill_details(119, "S", "2740", update_date="2026-01-01")
    n = len(fake.calls)
    assert n == 6
    assert client.bill_details(119, "s", 2740, update_date="2026-01-01") == first
    assert len(fake.calls) == n
    client.bill_details(119, "s", 2740, update_date="2026-02-01")
    assert len(fake.calls) == 2 * n


def test_normalize_bill(tmp_path):
    fake = FakeCongress({}, extra=details(119, "s", 2740, "RAISE Act of 2025"))
    row = normalize_bill(congress_client(tmp_path, fake).bill_details(119, "s", "2740"))
    assert row["source"] == "congress"
    assert (row["congress"], row["type"], row["number"]) == (119, "s", "2740")
    assert row["identifier"] == "S. 2740"
    assert row["record_id"] == "us-119-s-2740"
    assert row["url"] == "https://www.congress.gov/bill/119th-congress/senate-bill/2740"
    assert row["official_title"] == "A bill about RAISE Act of 2025."
    assert row["latest_action"] == {"date": "2025-09-09", "text": "Read twice and referred."}
    assert [a["classification"] for a in row["actions"]] == [
        ["reading-2", "referral-committee"],
        ["introduction"],
    ]
    assert [(s["name"], s["role"]) for s in row["sponsors"]] == [
        ("Sen. Doe, Jane [D-XX]", "sponsor"),
        ("Sen. Roe, Rick [R-YY]", "cosponsor"),
    ]
    assert row["texts"] == [
        {
            "type": "Introduced in Senate",
            "date": "2025-09-09",
            "url": "https://www.congress.gov/x.htm",
            "pdf_url": "https://www.congress.gov/x.pdf",
            "xml_url": None,
        }
    ]
    assert row["summary"] == "Act\nCovers artificial intelligence."
    assert row["summary_version"] == "Reported to Senate"
    assert row["external_ids"] == {"congress_gov": "119/s/2740"}


def test_ordinal_and_urls():
    assert [ordinal(n) for n in (1, 2, 3, 11, 12, 13, 21, 118, 119, 122)] == [
        "1st",
        "2nd",
        "3rd",
        "11th",
        "12th",
        "13th",
        "21st",
        "118th",
        "119th",
        "122nd",
    ]
    assert congress_gov_url(118, "HJRES", 5) == (
        "https://www.congress.gov/bill/118th-congress/house-joint-resolution/5"
    )


def test_strip_html():
    assert strip_html("<p>A&amp;B</p><ul><li>one</li><li>two</li></ul>") == "A&B\none\ntwo"
    assert strip_html(None) is None


# -- Federal Register fake API ------------------------------------------------


def fr_doc(
    number,
    title,
    *,
    eo=None,
    agency="Education Department",
    slug="education-department",
    dtype="Rule",
):
    return {
        "document_number": number,
        "type": "Presidential Document" if eo else dtype,
        "subtype": "Executive Order" if eo else None,
        "title": title,
        "publication_date": f"{number[:4]}-06-01",
        "signing_date": f"{number[:4]}-05-28" if eo else None,
        "executive_order_number": eo,
        "agencies": [{"name": agency, "slug": slug}],
        "html_url": f"https://www.federalregister.gov/documents/{number[:4]}/06/01/{number}/x",
        "pdf_url": f"https://www.govinfo.gov/content/pkg/FR-{number[:4]}-06-01/pdf/{number}.pdf",
        "abstract": None,
    }


class FakeFR:
    def __init__(self, eos, ed, page_size=None, memos=()):
        self.eos, self.ed, self.memos = eos, ed, list(memos)
        self.page_size = page_size
        self.calls = []

    def __call__(self, url, params, headers):
        assert url.startswith("https://www.federalregister.gov/api/v1/documents.json?")
        assert params == {}
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.calls.append(q)
        assert q["conditions[term]"] == ['"artificial intelligence"']
        assert q["conditions[publication_date][gte]"] == ["2023-01-01"]
        assert "document_number" in q["fields[]"]
        if q["conditions[type][]"] == ["PRESDOCU"]:
            kind = q["conditions[presidential_document_type][]"]
            docs = self.memos if kind == ["memorandum"] else self.eos
        else:
            docs = self.ed
        if docs is self.ed:
            assert q["conditions[agencies][]"] == ["education-department"]
            assert q["conditions[type][]"] == ["RULE", "PRORULE", "NOTICE"]
        size = self.page_size or int(q["per_page"][0])
        page = int(q["page"][0])
        chunk = docs[(page - 1) * size : page * size]
        pages = max(1, -(-len(docs) // size))
        return json.dumps({"count": len(docs), "total_pages": pages, "results": chunk}).encode()


def test_fr_search_paginates_and_caches(tmp_path):
    eos = [fr_doc(f"2025-0{i}000", f"EO {i}", eo=str(14000 + i)) for i in range(1, 6)]
    fake = FakeFR(eos, [], page_size=2)
    client = FederalRegisterClient(tmp_path, transport=fake, limiter=no_wait(), now=lambda: NOW)
    got = list(client.search(executive_orders(date(2023, 1, 1))))
    assert [d["executive_order_number"] for d in got] == [
        "14001",
        "14002",
        "14003",
        "14004",
        "14005",
    ]
    assert len(fake.calls) == 3
    list(client.search(executive_orders(date(2023, 1, 1))))
    assert len(fake.calls) == 3 and client.cache_hits == 3


def test_normalize_document():
    row = normalize_document(
        fr_doc("2025-07368", "Advancing AI Education", eo=14277), "executive-orders"
    )
    assert row["source"] == "federal_register"
    assert row["executive_order_number"] == "14277"
    assert row["agency"] == "Education Department"
    assert row["signing_date"] == "2025-05-28"
    assert row["search"] == "executive-orders"
    assert set(row) >= {
        "document_number",
        "type",
        "title",
        "publication_date",
        "signing_date",
        "executive_order_number",
        "agency",
        "html_url",
        "pdf_url",
        "abstract",
    }


def test_presidential_memoranda_search():
    s = presidential_memoranda(date(2023, 1, 1))
    assert s.name == "presidential-memoranda"
    assert ("conditions[presidential_document_type][]", "memorandum") in s.conditions
    assert ("conditions[type][]", "PRESDOCU") in s.conditions


def test_agency_search_name():
    s = agency_documents("education-department", date(2023, 1, 1))
    assert s.name == "education-department:rule,prorule,notice"


# -- existing records and the full run ----------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    write(
        d / "legislation/us/119/s-2740.yaml",
        "id: us-119-s-2740\nexternal_ids:\n  congress_gov: 119/s/2740\n",
    )
    write(d / "legislation/us/118/hr-6791.yaml", "id: us-118-hr-6791\n")  # path fallback
    write(d / "legislation/us/117/hr-1.yaml", "id: us-117-hr-1\n")  # outside the window
    write(
        d / "executive-actions/us/eo-14277.yaml",
        "id: us-eo-14277\nnumber: EO 14277\nexternal_ids:\n  federal_register_document_number: 2025-07368\n",
    )
    write(
        d / "executive-actions/us/eo-14110.yaml",
        "id: us-eo-14110\nnumber: EO 14110\nsources:\n"
        "  - url: https://www.federalregister.gov/documents/2023/11/01/2023-24283/safe\n"
        "  - url: https://www.federalregister.gov/documents/2025/01/28/2025-01901/rescissions\n",
    )
    write(
        d / "documents/us/ed-final.yaml",
        "id: us-ed-final\nsources:\n"
        "  - url: https://www.federalregister.gov/documents/2026/04/13/2026-07087/final-priority\n",
    )
    return d


def test_build_index(data_dir):
    index = build_index(data_dir)
    assert index.match_bill(119, "S", "2740") == "us-119-s-2740"
    assert index.match_bill(118, "hr", 6791) == "us-118-hr-6791"
    assert index.match_bill(119, "hr", "1") is None
    assert index.match_document("2025-07368", None) == "us-eo-14277"
    assert index.match_document(None, "14110") == "us-eo-14110"
    assert index.match_document("2023-24283", None) == "us-eo-14110"
    assert index.match_document("2026-07087", None) == "us-ed-final"
    # Only the first source identifies the record; later ones cite related documents.
    assert index.match_document("2025-01901", "14148") is None


def test_fr_document_number():
    assert fr_document_number("https://www.federalregister.gov/d/2025-07368") == "2025-07368"
    assert (
        fr_document_number("https://www.govinfo.gov/content/pkg/FR-2023-02-22/pdf/2023-03779.pdf")
        == "2023-03779"
    )
    assert fr_document_number("https://www.ed.gov/x.pdf") is None


def test_find_federal_candidates(tmp_path, data_dir):
    bills = {
        118: [
            list_bill(118, "hr", 6791, "Artificial Intelligence Literacy Act of 2023"),
            list_bill(118, "hr", 1, "Lower Energy Costs Act"),
        ],
        119: [
            list_bill(119, "s", 2740, "RAISE Act of 2025"),  # found as an existing record
            list_bill(119, "hr", 5, "Deepfake Accountability Act"),
            list_bill(119, "hr", 6, "Farm Bill"),  # found through its summary
            list_bill(119, "hr", 7, "Hawaiian ai Act"),  # lower-case "ai" is not a match
        ],
    }
    summaries = {
        119: [
            {
                "bill": {"type": "HR", "number": "6", "title": "Farm Bill"},
                "text": "<p>Uses machine learning.</p>",
            },
            {
                "bill": {"type": "HR", "number": "7", "title": "Hawaiian ai Act"},
                "text": "<p>Nothing.</p>",
            },
        ]
    }
    routes = {}
    routes.update(details(118, "hr", 6791, "Artificial Intelligence Literacy Act of 2023"))
    routes.update(details(119, "s", 2740, "RAISE Act of 2025"))
    routes.update(details(119, "hr", 5, "Deepfake Accountability Act"))
    routes.update(details(119, "hr", 6, "Farm Bill"))
    congress = congress_client(tmp_path, FakeCongress(bills, summaries, routes))
    fr = FederalRegisterClient(
        tmp_path / "fr",
        transport=FakeFR(
            [
                fr_doc("2023-24283", "Safe AI", eo="14110"),
                fr_doc("2025-01901", "Rescissions", eo="14148"),
            ],
            [
                fr_doc("2026-07087", "Final Priority AI"),
                fr_doc("2025-13650", "Proposed Priority AI", dtype="Proposed Rule"),
            ],
            memos=[fr_doc("2025-02172", "AI in Federal Agencies", dtype="Presidential Document")],
        ),
        limiter=no_wait(),
        now=lambda: NOW,
    )
    out = tmp_path / "candidates"
    result = find_federal_candidates(
        data_dir, out, congress=congress, federal_register=fr, today=date(2026, 10, 17)
    )
    assert result.path == out / "federal-2026-10-17.jsonl"
    rows = [json.loads(line) for line in result.path.read_text().splitlines()]
    bills_out = [r for r in rows if r["source"] == "congress"]
    docs_out = [r for r in rows if r["source"] == "federal_register"]
    assert [(r["congress"], r["type"], r["number"], r["existing_record"]) for r in bills_out] == [
        (118, "hr", "6791", "us-118-hr-6791"),
        (119, "hr", "5", None),
        (119, "hr", "6", None),
        (119, "s", "2740", "us-119-s-2740"),
    ]
    by_number = {r["number"]: r for r in bills_out}
    assert by_number["6"]["matched_in"] == ["summary"]
    assert by_number["2740"]["matched_in"] == ["existing"]
    assert by_number["2740"]["matched_keywords"] == ["artificial intelligence"]  # from the summary
    assert by_number["5"]["matched_keywords"] == ["artificial intelligence", "deepfake"]
    assert [(r["document_number"], r["existing_record"]) for r in docs_out] == [
        ("2023-24283", "us-eo-14110"),
        ("2025-01901", None),
        ("2025-02172", None),
        ("2025-13650", None),
        ("2026-07087", "us-ed-final"),
    ]
    assert result.bills_by_congress == {118: 1, 119: 3}
    assert result.documents_by_type == {
        "executive-order": 2,
        "presidential-memorandum": 1,
        "Rule": 1,
        "Proposed Rule": 1,
    }
    assert result.existing == ["us-118-hr-6791", "us-119-s-2740", "us-ed-final", "us-eo-14110"]
    # EO 14277 is in data/ but the fake search did not return it; hr-1 (117th) is out of window.
    assert result.missing_existing == ["us-eo-14277"]
    summary = json.loads(result.path.with_suffix(".summary.json").read_text())
    assert summary["bills"] == 4 and summary["documents"] == 5
    assert not (data_dir / "legislation/us/119/hr-5.yaml").exists()


def test_find_federal_candidates_one_source(tmp_path, data_dir):
    fr = FederalRegisterClient(
        tmp_path / "fr", transport=FakeFR([], []), limiter=no_wait(), now=lambda: NOW
    )
    result = find_federal_candidates(
        data_dir, tmp_path / "c", congress=None, federal_register=fr, today=date(2026, 10, 17)
    )
    assert result.bills == 0 and result.documents == 0
    assert "us-119-s-2740" not in result.missing_existing


def test_titles_channel_matches_and_caches(tmp_path, data_dir):
    from edictai.federal.run import FederalResult, discover_bills

    def titles(*names):
        return {
            "titles": [{"title": n, "titleType": "Official Title as Introduced"} for n in names],
            "pagination": {"count": len(names)},
        }

    listing = {
        119: [
            list_bill(119, "s", 2741, "SAFE Kids Act"),
            list_bill(119, "hr", 11, "Farm Bill"),
            list_bill(119, "hr", 5, "Deepfake Accountability Act"),  # matched by title already
        ]
    }
    routes = {
        "/bill/119/s/2741/titles": titles(
            "SAFE Kids Act", "To regulate artificial intelligence companions for minors."
        ),
        "/bill/119/hr/11/titles": titles("Farm Bill", "To reauthorize agricultural programs."),
    }
    fake = FakeCongress(listing, {}, routes)
    client = congress_client(tmp_path, fake)
    index = build_index(data_dir)

    def run():
        result = FederalResult(path=tmp_path / "x.jsonl")
        hits = discover_bills(
            client, 119, date(2023, 1, 1), index, result, lambda _m: None, titles=True
        )
        return hits, result

    hits, result = run()
    assert hits[(119, "s", "2741")].matched_in == {"titles"}
    assert (119, "hr", "11") not in hits
    assert hits[(119, "hr", "5")].matched_in == {"title"}
    assert result.titles_checked[119] == 2
    title_calls = [p for p, _ in fake.calls if p.endswith("/titles")]
    assert sorted(title_calls) == ["/bill/119/hr/11/titles", "/bill/119/s/2741/titles"]
    # A second pass reads every bill's titles from the cache.
    run()
    assert len([p for p, _ in fake.calls if p.endswith("/titles")]) == 2
    # Once the listing is refreshed, an updated bill is fetched again.
    listing[119][0]["updateDateIncludingText"] = "2026-09-30"
    client = CongressClient(
        KEY,
        tmp_path / "congress",
        transport=fake,
        limiter=no_wait(),
        now=lambda: NOW + timedelta(days=1),
    )
    run()
    assert len([p for p, _ in fake.calls if p.endswith("/titles")]) == 3


def test_title_requests_cap_defers_the_rest_to_a_later_run(tmp_path, data_dir):
    from edictai.federal.run import FederalResult, discover_bills

    listing = {119: [list_bill(119, "hr", n, "Farm Bill") for n in (1, 2, 3)]}
    routes = {
        f"/bill/119/hr/{n}/titles": {"titles": [{"title": "Farm Bill"}], "pagination": {"count": 1}}
        for n in (1, 2, 3)
    }
    fake = FakeCongress(listing, {}, routes)
    client = congress_client(tmp_path, fake)
    index = build_index(data_dir)

    def run(cap):
        result = FederalResult(path=tmp_path / "x.jsonl", title_requests_left=cap)
        discover_bills(client, 119, date(2023, 1, 1), index, result, lambda _m: None, titles=True)
        return result

    first = run(2)
    assert (first.titles_checked[119], first.titles_deferred[119]) == (2, 1)
    # The next run answers the first two from the cache and spends its one request on the third.
    second = run(1)
    assert (second.titles_checked[119], second.titles_deferred[119]) == (3, 0)
    assert len([p for p, _ in fake.calls if p.endswith("/titles")]) == 3


def test_titles_channel_off_by_default(tmp_path, data_dir):
    from edictai.federal.run import FederalResult, discover_bills

    fake = FakeCongress({119: [list_bill(119, "hr", 11, "Farm Bill")]}, {}, {})
    client = congress_client(tmp_path, fake)
    result = FederalResult(path=tmp_path / "x.jsonl")
    discover_bills(client, 119, date(2023, 1, 1), build_index(data_dir), result, lambda _m: None)
    assert not any(p.endswith("/titles") for p, _ in fake.calls)
