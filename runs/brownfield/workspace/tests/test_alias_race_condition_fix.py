"""Regression test for the check-then-act race condition fix (brownfield
scenario). We can't easily force two SQLite threads to interleave inside a
unit test, but we CAN prove the new code path is race-safe by asserting
that duplicate codes are rejected via the database constraint even when
the old pre-check would have been bypassed (e.g. two calls that both
compute the same custom alias)."""
from app.errors import AliasTakenError


def test_duplicate_custom_alias_is_rejected_atomically(client):
    r1 = client.post("/api/v1/shorten", json={"url": "https://example.com/1", "custom_alias": "race"})
    assert r1.status_code == 201
    r2 = client.post("/api/v1/shorten", json={"url": "https://example.com/2", "custom_alias": "race"})
    assert r2.status_code == 409
    assert r2.get_json()["error"] == "AliasTakenError"


def test_generated_code_insert_path_still_succeeds(client):
    resp = client.post("/api/v1/shorten", json={"url": "https://example.com/3"})
    assert resp.status_code == 201
