# tests/test_api.py
"""API tests with mocked externals (no real Groq/GitHub/network)."""

import pytest
from fastapi.testclient import TestClient

from src import hindsight, main
from src.models import FailureRecord
from src.store import store


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Isolate store and hindsight to temp files; reset store records."""
    monkeypatch.setattr(store, "_path", str(tmp_path / "store.json"))
    monkeypatch.setattr(store, "_records", {})
    monkeypatch.setattr(hindsight, "_db_file", lambda: str(tmp_path / "hs.json"))
    monkeypatch.setattr(main, "retain_successful_fix", lambda *a, **k: "key")
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


def _seed(run_id=111, status="awaiting_review"):
    record = FailureRecord(
        run_id=run_id,
        repo="owner/repo",
        status=status,
        log_excerpt="Traceback... AssertionError",
        suggested_fix="### Fix\nDo the thing.",
        source="generated",
        model_tier="reasoning",
    )
    store.upsert(record)
    return record


def test_health_reports_dependency_booleans(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["dependencies"]) == {"groq_key_present", "github_token_present"}
    # Never leak secret values, only booleans.
    assert isinstance(body["dependencies"]["groq_key_present"], bool)


def test_list_and_get_failures(client):
    _seed(run_id=111)
    _seed(run_id=222)

    resp = client.get("/api/failures")
    assert resp.status_code == 200
    ids = [r["run_id"] for r in resp.json()]
    assert 111 in ids and 222 in ids

    detail = client.get("/api/failures/111")
    assert detail.status_code == 200
    assert detail.json()["run_id"] == 111

    missing = client.get("/api/failures/999")
    assert missing.status_code == 404


def test_approve_with_edited_fix(client):
    _seed(run_id=111)
    resp = client.post(
        "/api/failures/111/approve", json={"edited_fix": "### Edited\nNew fix."}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["suggested_fix"] == "### Edited\nNew fix."


def test_approve_then_reject_conflicts(client):
    _seed(run_id=111)
    assert client.post("/api/failures/111/approve", json={}).status_code == 200
    # Already approved → cannot reject.
    conflict = client.post("/api/failures/111/reject", json={"reason": "no"})
    assert conflict.status_code == 409


def test_reject_records_reason(client):
    _seed(run_id=333)
    resp = client.post("/api/failures/333/reject", json={"reason": "wrong fix"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["reject_reason"] == "wrong fix"


def test_stats_counts(client):
    _seed(run_id=111, status="approved")
    _seed(run_id=222, status="rejected")
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_failures"] == 2
    assert body["approved"] == 1
    assert body["rejected"] == 1


def test_webhook_ignores_non_failure(client):
    resp = client.post("/webhook", json={"workflow_run": {"conclusion": "success"}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_webhook_accepts_failure_without_running_real_work(client, monkeypatch):
    called = {}

    async def fake_handle(run_id, repo):
        called["run_id"] = run_id

    monkeypatch.setattr(main, "handle_failure", fake_handle)
    resp = client.post(
        "/webhook",
        json={
            "workflow_run": {"id": 555, "conclusion": "failure"},
            "repository": {"full_name": "owner/repo"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert called.get("run_id") == 555


def test_memory_endpoints(client, monkeypatch):
    # Use the real (temp) hindsight for these.
    monkeypatch.setattr(main, "list_entries", hindsight.list_entries)
    monkeypatch.setattr(main, "delete_entry", hindsight.delete_entry)
    hindsight.retain_successful_fix("some error signature", "the fix")

    listing = client.get("/api/memory")
    assert listing.status_code == 200
    keys = list(listing.json().keys())
    assert keys

    deleted = client.delete(f"/api/memory/{keys[0]}")
    assert deleted.status_code == 200
