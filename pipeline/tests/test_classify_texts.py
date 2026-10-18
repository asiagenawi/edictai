import base64

import pytest

from statescope.classify.common import ClassifyPaths
from statescope.classify.texts import TextStore, earlier_versions, fetch_texts, mentions_ai
from statescope.paths import Paths

LATEST = (
    "<html><body><p>AN ACT relating to community college faculty.</p>"
    "<p>Section 1. An instructor of record shall meet the minimum qualifications.</p>"
    "</body></html>"
)
INTRODUCED = (
    "<html><body><p>AN ACT relating to community college faculty.</p>"
    "<p>Section 1. Artificial intelligence shall not be used to replace community college "
    "faculty for the purposes of academic instruction.</p></body></html>"
)
AMENDED = (
    "<html><body><p>AN ACT relating to community college faculty.</p>"
    "<p>Section 1. Faculty shall be consulted on the adoption of new instructional tools "
    "and course materials.</p></body></html>"
)


class FakeUsage:
    def __init__(self):
        self.n = 0

    def used(self):
        return self.n


class FakeClient:
    def __init__(self, cache_dir, docs):
        self.cache_dir = cache_dir
        self.docs = docs
        self.usage = FakeUsage()
        self.calls = []

    def call(self, op, **params):
        self.usage.n += 1
        self.calls.append(params["id"])
        html = self.docs[params["id"]]
        return {
            "status": "OK",
            "text": {
                "doc_id": params["id"],
                "mime": "text/html",
                "doc": base64.b64encode(html.encode()).decode(),
            },
        }


def bill(*docs):
    return {
        "bill_id": 9,
        "texts": [
            {
                "doc_id": d,
                "type": t,
                "date": day,
                "mime": "text/html",
                "state_link": f"https://leg.example.gov/{d}.htm",
            }
            for d, t, day in docs
        ],
    }


BILL = bill(
    (1, "Introduced", "2024-02-01"), (2, "Amended", "2024-03-01"), (3, "Enrolled", "2024-08-01")
)


@pytest.fixture
def cp(tmp_path):
    (tmp_path / "data").mkdir()
    return ClassifyPaths(Paths(tmp_path))


def test_mentions_ai_ignores_weak_keywords():
    assert mentions_ai("Use of generative tools in schools")
    assert not mentions_ai("a pricing algorithm for rents")
    assert not mentions_ai(None)


def test_earlier_versions_order_and_exclude_latest():
    assert [t["doc_id"] for t in earlier_versions(BILL["texts"], 3, "earliest")] == [1, 2]
    assert [t["doc_id"] for t in earlier_versions(BILL["texts"], 3, "ai")] == [2, 1]
    assert earlier_versions(bill((5, "Introduced", "2024-01-01"))["texts"], 5, "earliest") == []


def test_fetch_texts_latest_only_by_default(cp):
    client = FakeClient(cp.legiscan, {1: INTRODUCED, 2: AMENDED, 3: LATEST})
    result = fetch_texts(cp, client, [("k", BILL)], max_queries=10)
    assert client.calls == [3] and result.earlier == []
    store = TextStore(cp.texts)
    assert store.earlier_for_key("k") == (None, None)
    assert len(store.texts_for_key("k")) == 1


def test_fetch_texts_earliest_stores_beside_latest(cp):
    client = FakeClient(cp.legiscan, {1: INTRODUCED, 2: AMENDED, 3: LATEST})
    result = fetch_texts(cp, client, [("k", BILL)], max_queries=10, version="earliest")
    assert client.calls == [3, 1] and result.earlier == ["k"] and result.queries == 2
    store = TextStore(cp.texts)
    meta, text = store.for_key("k")
    assert meta["doc_id"] == 3 and "minimum qualifications" in text
    emeta, etext = store.earlier_for_key("k")
    assert emeta["doc_id"] == 1 and emeta["type"] == "Introduced"
    assert emeta["state_link"] == "https://leg.example.gov/1.htm"
    assert "Artificial intelligence shall not be used" in etext
    assert [m["doc_id"] for m, _ in store.texts_for_key("k")] == [3, 1]
    # Everything is cached now: a second run sends no queries.
    again = FakeClient(cp.legiscan, {})
    fetch_texts(cp, again, [("k", BILL)], max_queries=10, version="earliest")
    assert again.calls == []


def test_fetch_texts_ai_takes_newest_earlier_version_that_mentions_ai(cp):
    client = FakeClient(cp.legiscan, {1: INTRODUCED, 2: AMENDED, 3: LATEST})
    result = fetch_texts(cp, client, [("k", BILL)], max_queries=10, version="ai")
    assert client.calls == [3, 2, 1] and result.earlier == ["k"]
    assert TextStore(cp.texts).earlier_for_key("k")[0]["doc_id"] == 1


def test_fetch_texts_ai_without_match_and_single_version(cp):
    client = FakeClient(cp.legiscan, {1: AMENDED, 3: LATEST, 5: LATEST})
    two = bill((1, "Introduced", "2024-02-01"), (3, "Enrolled", "2024-08-01"))
    one = bill((5, "Introduced", "2024-01-01"))
    result = fetch_texts(cp, client, [("a", two), ("b", one)], max_queries=10, version="ai")
    assert result.earlier == []
    assert dict(result.no_earlier) == {
        "a": "no earlier version mentions AI",
        "b": "only one version",
    }
    assert TextStore(cp.texts).earlier_for_key("a") == (None, None)


def test_fetch_texts_earlier_respects_budget(cp):
    client = FakeClient(cp.legiscan, {1: INTRODUCED, 2: AMENDED, 3: LATEST})
    result = fetch_texts(cp, client, [("k", BILL)], max_queries=1, version="earliest")
    assert client.calls == [3] and result.skipped_budget == ["k"] and result.earlier == []
    assert TextStore(cp.texts).for_key("k")[0]["doc_id"] == 3
