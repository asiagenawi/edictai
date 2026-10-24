import json
import zipfile

from edictai.ingest.fulltext import dataset_bill_index, run_searches

from .test_ingest_legiscan import SearchAPI, client


def test_run_searches_pages_and_fetches_missing_bills(tmp_path):
    api = SearchAPI()
    c = client(tmp_path, api)
    datasets = tmp_path / "legiscan" / "datasets"
    datasets.mkdir(parents=True)
    with zipfile.ZipFile(datasets / "7.zip", "w") as zf:
        zf.writestr("TX/2025/bill/HB1.json", json.dumps({"bill": {"bill_id": 11}}))
    (datasets / "7.json").write_text(json.dumps({"dataset_hash": "h7"}))

    result = run_searches(c, [2025, 2026], searches=["q1", "q2"], states=["ALL"])
    searches = [p for p in api.calls if p["op"] == "getSearchRaw"]
    assert len(searches) == 8  # 2 queries x 2 years x 2 pages
    assert sorted(result.hits) == [11, 12]
    assert result.hits[11]["queries"] == ["q1", "q2"]
    assert result.hits[11]["source"] == "dataset"
    assert result.fetched_bills == [12]  # only the bill missing from the datasets
    assert result.queries == 9
    saved = json.loads((tmp_path / "legiscan" / "fulltext.json").read_text())
    assert saved["searches"] == ["q1", "q2"] and set(saved["hits"]) == {"11", "12"}

    again = run_searches(c, [2025, 2026], searches=["q1", "q2"], states=["ALL"])
    assert again.queries == 0


def test_bill_index_is_rebuilt_when_hash_changes(tmp_path):
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    with zipfile.ZipFile(datasets / "7.zip", "w") as zf:
        zf.writestr("TX/2025/bill/HB1.json", json.dumps({"bill": {"bill_id": 11}}))
    (datasets / "7.json").write_text(json.dumps({"dataset_hash": "h7"}))
    assert dataset_bill_index(tmp_path) == {11: (7, "TX/2025/bill/HB1.json")}
    with zipfile.ZipFile(datasets / "7.zip", "w") as zf:
        zf.writestr("TX/2025/bill/HB2.json", json.dumps({"bill": {"bill_id": 22}}))
    assert dataset_bill_index(tmp_path) == {11: (7, "TX/2025/bill/HB1.json")}  # same hash
    (datasets / "7.json").write_text(json.dumps({"dataset_hash": "h8"}))
    assert dataset_bill_index(tmp_path) == {22: (7, "TX/2025/bill/HB2.json")}
