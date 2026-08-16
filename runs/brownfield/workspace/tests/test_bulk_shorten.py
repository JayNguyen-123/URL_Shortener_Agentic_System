def test_bulk_shorten_creates_multiple_links(client):
    resp = client.post("/api/v1/shorten/bulk", json={"urls": [
        "https://example.com/1", "https://example.com/2",
    ]})
    assert resp.status_code == 201
    results = resp.get_json()["results"]
    assert len(results) == 2
    assert all("code" in r for r in results)


def test_bulk_shorten_reports_per_item_errors_without_failing_whole_batch(client):
    resp = client.post("/api/v1/shorten/bulk", json={"urls": [
        "https://example.com/ok", "not-a-url",
    ]})
    assert resp.status_code == 201
    results = resp.get_json()["results"]
    assert "code" in results[0]
    assert results[1]["error"] == "InvalidURLError"


def test_bulk_shorten_rejects_oversized_batch(client):
    from app.config import Config
    urls = ["https://example.com"] * (Config.MAX_BULK_URLS + 1)
    resp = client.post("/api/v1/shorten/bulk", json={"urls": urls})
    assert resp.status_code == 400


def test_bulk_shorten_rejects_empty_list(client):
    resp = client.post("/api/v1/shorten/bulk", json={"urls": []})
    assert resp.status_code == 400
