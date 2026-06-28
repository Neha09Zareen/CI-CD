# tests/test_hindsight.py
"""Unit tests for Hindsight memory: signature stability and round-trip."""

import pytest

from src import hindsight


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point Hindsight at a temporary JSON file for isolation."""
    db = tmp_path / "hindsight_test.json"
    monkeypatch.setattr(hindsight, "_db_file", lambda: str(db))
    return db


def test_signature_is_deterministic():
    a = hindsight.signature_for("Error:   something   broke\n  badly")
    b = hindsight.signature_for("Error: something broke badly")
    # Whitespace normalization makes these identical.
    assert a == b


def test_signature_bounded_length():
    sig = hindsight.signature_for("x" * 500)
    assert len(sig) <= 100


def test_retain_then_recall_round_trip(temp_db):
    # Property 1: a fix retained under signature_for(x) is recalled for x.
    chunk = "Traceback (most recent call last):\nAssertionError: 1 + 1 != 3"
    fix = "Fix: correct the assertion to 1 + 1 == 2"
    key = hindsight.retain_successful_fix(chunk, fix)
    assert key

    recalled = hindsight.recall_historical_fix(chunk)
    assert recalled == fix


def test_recall_miss_returns_none(temp_db):
    assert hindsight.recall_historical_fix("totally novel error xyz") is None


def test_recall_matches_signature_within_larger_text(temp_db):
    short = "ModuleNotFoundError: No module named 'foo'"
    hindsight.retain_successful_fix(short, "pip install foo")
    # A later, longer log containing the same error should still match.
    longer = f"some preamble\n{short}\nmore trailing context here"
    assert hindsight.recall_historical_fix(longer) == "pip install foo"


def test_signature_ignores_chunk_headers_and_bom():
    # The same error in different chunk positions must map to one signature.
    a = hindsight.signature_for("--- LOG CHUNK 1 ---\n\ufeffAssertionError: boom")
    b = hindsight.signature_for("--- LOG CHUNK 7 ---\nAssertionError: boom")
    assert a == b
    assert "LOG CHUNK" not in a


def test_recall_matches_across_chunk_positions(temp_db):
    hindsight.retain_successful_fix(
        "--- LOG CHUNK 1 ---\nKeyError: 'missing'", "handle the key"
    )
    # Same error later appears as a different chunk number.
    assert (
        hindsight.recall_historical_fix("--- LOG CHUNK 4 ---\nKeyError: 'missing'")
        == "handle the key"
    )


def test_list_and_delete(temp_db):
    key = hindsight.retain_successful_fix("error alpha here", "fix alpha")
    entries = hindsight.list_entries()
    assert key in entries

    assert hindsight.delete_entry(key) is True
    assert key not in hindsight.list_entries()
    assert hindsight.delete_entry(key) is False
