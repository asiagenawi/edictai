import base64
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest

from statescope.ingest.http import RateLimiter
from statescope.ingest.legiscan import (
    LegiScanClient,
    LegiScanError,
    QuotaExceeded,
    Usage,
    bulk_download,
)

KEY = "test-key-not-real"


def make_zip(files: dict[str, object]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, json.dumps(data))
    return buf.getvalue()


def entry(session_id, state_id, year_start, year_end=None, special=0, tag="Regular Session"):
    return {
        "state_id": state_id,
        "session_id": session_id,
        "year_start": year_start,
        "year_end": year_end or year_start,
        "special": special,
        "session_tag": tag,
        "session_name": f"{year_start} {tag}",
        "session_title": f"{year_start} {tag}",
        "dataset_hash": f"hash-{session_id}",
        "dataset_date": "2026-10-11",
        "access_key": f"ak{session_id}",
    }


class FakeAPI:
    def __init__(self, datasets):
        self.datasets = datasets
        self.calls = []

    def __call__(self, url, params, headers):
        assert url == "https://api.legiscan.com/"
        assert params["key"] == KEY
        self.calls.append(dict(params))
        if params["op"] == "getDatasetList":
            return json.dumps({"status": "OK", "datasetlist": self.datasets}).encode()
        if params["op"] == "getDataset":
            sid = int(params["id"])
            e = next(d for d in self.datasets if d["session_id"] == sid)
            assert params["access_key"] == e["access_key"]
            raw = make_zip({f"XX/{sid}/bill/1.json": {"bill": {"bill_id": sid}}})
            return json.dumps(
                {
                    "status": "OK",
                    "dataset": {
                        "session_id": sid,
                        "dataset_hash": e["dataset_hash"],
                        "zip": base64.b64encode(raw).decode(),
                    },
                }
            ).encode()
        return json.dumps({"status": "ERROR", "alert": {"message": "Unknown op"}}).encode()

    def ops(self):
        return [c["op"] for c in self.calls]


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def client(tmp_path, api, *, limit=8000, today=date(2026, 10, 17), clock=None):
    clock = clock or FakeClock()
    return LegiScanClient(
        KEY,
        tmp_path / "legiscan",
        transport=api,
        limiter=RateLimiter(1.0, clock=clock.clock, sleep=clock.sleep),
        usage=Usage(tmp_path / "legiscan" / "usage.json", limit, today=lambda: today),
    )


DATASETS = [
    entry(1, 43, 2023),  # TX 2023
    entry(2, 43, 2023, special=1, tag="1st Special Session"),
    entry(3, 5, 2023, 2024),  # CA 2023-2024
    entry(4, 52, 2025, 2026),  # US 119th
    entry(5, 43, 2021),  # before the window
]


def test_bulk_download_fetches_overlapping_sessions_once(tmp_path):
    api = FakeAPI(DATASETS)
    c = client(tmp_path, api)
    result = bulk_download(c, date(2023, 1, 1), date(2026, 10, 17))
    assert api.ops() == ["getDatasetList"] + ["getDataset"] * 4
    assert sorted(int(c["id"]) for c in api.calls[1:]) == [1, 2, 3, 4]
    assert result.queries == 5
    idents = {(d["state"], d["identifier"]) for d in result.downloaded}
    assert idents == {("TX", "2023"), ("TX", "2023-s1"), ("CA", "2023-2024"), ("US", "119")}
    archive = tmp_path / "legiscan" / "datasets" / "1.zip"
    assert zipfile.ZipFile(archive).namelist() == ["XX/1/bill/1.json"]
    meta = json.loads((tmp_path / "legiscan" / "datasets" / "1.json").read_text())
    assert meta["dataset_hash"] == "hash-1" and "zip" not in meta

    # A second run reuses the week-old list and skips unchanged hashes: no queries.
    again = bulk_download(c, date(2023, 1, 1), date(2026, 10, 17))
    assert api.ops().count("getDataset") == 4 and len(api.calls) == 5
    assert again.queries == 0 and len(again.unchanged) == 4


def test_changed_hash_is_downloaded_again(tmp_path):
    api = FakeAPI([dict(d) for d in DATASETS])
    c = client(tmp_path, api)
    bulk_download(c, date(2023, 1, 1), date(2026, 10, 17))
    # Expire the cached list and change one hash.
    list_path = tmp_path / "legiscan" / "responses" / "getDatasetList.json"
    cached = json.loads(list_path.read_text())
    cached["fetched_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    list_path.write_text(json.dumps(cached))
    api.datasets[0]["dataset_hash"] = "hash-1-new"
    result = bulk_download(c, date(2023, 1, 1), date(2026, 10, 17))
    assert [d["session_id"] for d in result.downloaded] == [1]
    assert result.queries == 2


def test_api_key_never_written_to_cache(tmp_path):
    api = FakeAPI(DATASETS)
    c = client(tmp_path, api)
    bulk_download(c, date(2023, 1, 1), date(2026, 10, 17))
    for path in (tmp_path / "legiscan").rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path
    assert KEY not in repr(c)


def test_status_error_raises(tmp_path):
    api = FakeAPI(DATASETS)
    c = client(tmp_path, api)
    with pytest.raises(LegiScanError, match="Unknown op"):
        c.call("getNothing")


def test_non_json_raises(tmp_path):
    c = client(tmp_path, lambda url, params, headers: b"<html>")
    with pytest.raises(LegiScanError, match="not JSON"):
        c.call("getDatasetList")


def test_rate_limit_spaces_requests(tmp_path):
    clock = FakeClock()
    c = client(tmp_path, FakeAPI(DATASETS), clock=clock)
    c.call("getDatasetList")
    c.call("getDatasetList")
    clock.now += 0.25
    c.call("getDatasetList")
    assert clock.slept == [1.0, 0.75]


def test_monthly_quota_is_a_hard_stop(tmp_path):
    api = FakeAPI(DATASETS)
    c = client(tmp_path, api, limit=2)
    c.call("getDatasetList")
    c.call("getDatasetList")
    with pytest.raises(QuotaExceeded):
        c.call("getDatasetList")
    assert len(api.calls) == 2
    usage = json.loads((tmp_path / "legiscan" / "usage.json").read_text())
    assert usage["2026-10"] == {"queries": 2, "by_op": {"getDatasetList": 2}}


def test_quota_resets_each_month(tmp_path):
    path = tmp_path / "usage.json"
    october = Usage(path, 1, today=lambda: date(2026, 10, 31))
    october.reserve("getDataset")
    with pytest.raises(QuotaExceeded):
        october.reserve("getDataset")
    november = Usage(path, 1, today=lambda: date(2026, 11, 1))
    assert november.reserve("getDataset") == 1


def test_bad_archive_is_rejected(tmp_path):
    def api(url, params, headers):
        if params["op"] == "getDatasetList":
            return json.dumps({"status": "OK", "datasetlist": DATASETS[:1]}).encode()
        return json.dumps({"status": "OK", "dataset": {"zip": "bm90IGEgemlw"}}).encode()

    c = client(tmp_path, api)
    with pytest.raises(LegiScanError, match="unreadable archive"):
        bulk_download(c, date(2023, 1, 1), date(2026, 10, 17))
    assert not (tmp_path / "legiscan" / "datasets" / "1.json").exists()


def test_missing_key(tmp_path):
    with pytest.raises(LegiScanError, match="not set"):
        LegiScanClient("", tmp_path)


class SearchAPI:
    """getSearchRaw over two pages, plus getBill."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, params, headers):
        self.calls.append(dict(params))
        op = params["op"]
        if op == "getSearchRaw":
            page = int(params["page"])
            rows = [{"relevance": 90, "bill_id": 10 + page, "change_hash": f"c{10 + page}"}]
            summary = {"page_total": 2, "page_current": page, "count": 2}
            return json.dumps(
                {"status": "OK", "searchresult": {"summary": summary, "results": rows}}
            ).encode()
        if op == "getBill":
            bid = int(params["id"])
            bill = {"bill_id": bid, "change_hash": f"c{bid}", "state": "TX"}
            return json.dumps({"status": "OK", "bill": bill}).encode()
        raise AssertionError(op)


def test_search_is_cached_for_at_least_an_hour(tmp_path):
    api = SearchAPI()
    c = client(tmp_path, api)
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    first = c.search_raw('"artificial intelligence"', state="ALL", year=2025, now=now)
    assert first["results"][0]["bill_id"] == 11
    # Even a shorter max_age cannot repeat an identical search within the hour.
    c.search_raw(
        '"artificial intelligence"',
        state="ALL",
        year=2025,
        now=now + timedelta(minutes=59),
        max_age=timedelta(minutes=1),
    )
    assert len(api.calls) == 1
    c.search_raw('"artificial intelligence"', state="ALL", year=2025, now=now + timedelta(days=2))
    assert len(api.calls) == 2
    c.search_raw('"artificial intelligence"', state="US", year=2025, now=now + timedelta(days=2))
    assert len(api.calls) == 3


def test_get_bill_refetches_only_on_new_change_hash(tmp_path):
    api = SearchAPI()
    c = client(tmp_path, api)
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    c.get_bill(5, "c5", now=now)
    c.get_bill(5, "c5", now=now + timedelta(days=1))  # same hash: cached
    c.get_bill(5, "other", now=now + timedelta(hours=1))  # within 3 hours: cached
    assert [p["op"] for p in api.calls] == ["getBill"]
    c.get_bill(5, "other", now=now + timedelta(hours=4))
    assert len(api.calls) == 2
