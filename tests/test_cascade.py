# tests/test_cascade.py
"""Unit tests for log chunking and culprit-chunk selection."""

import pytest

from src.cascade_parser import cascade_chunk_logs
from src.cascade_flow import select_culprit_chunk, _score_chunk


def _make_log(n_lines: int) -> str:
    return "\n".join(f"line {i}" for i in range(n_lines))


def test_empty_logs_return_no_chunks():
    assert cascade_chunk_logs("") == []


def test_small_log_single_chunk():
    chunks = cascade_chunk_logs(_make_log(10), max_lines=150, overlap=20)
    assert len(chunks) == 1
    assert "line 0" in chunks[0]
    assert "line 9" in chunks[0]


def test_overlap_must_be_less_than_max_lines():
    with pytest.raises(ValueError):
        cascade_chunk_logs(_make_log(100), max_lines=50, overlap=50)


def test_chunk_coverage_every_line_present():
    # Property 2: every input line appears in at least one chunk.
    log = _make_log(500)
    chunks = cascade_chunk_logs(log, max_lines=100, overlap=20)
    joined = "\n".join(chunks)
    for i in range(500):
        assert f"line {i}" in joined


def test_chunks_overlap_by_configured_amount():
    log = _make_log(300)
    max_lines, overlap = 100, 20
    chunks = cascade_chunk_logs(log, max_lines=max_lines, overlap=overlap)
    assert len(chunks) >= 2
    # The last `overlap` data lines of chunk 0 should reappear in chunk 1.
    first_lines = chunks[0].splitlines()[1:]  # drop the header line
    second_lines = chunks[1].splitlines()[1:]
    tail = first_lines[-overlap:]
    assert tail == second_lines[:overlap]


def test_select_culprit_picks_error_chunk():
    chunks = [
        "line a\nline b",
        "Traceback (most recent call last):\nException: boom",
        "line c\nline d",
    ]
    index, text = select_culprit_chunk(chunks)
    assert index == 1
    assert "Traceback" in text


def test_select_culprit_falls_back_to_last_chunk():
    # Property 3: no error signal anywhere → last chunk.
    chunks = ["alpha", "beta", "gamma"]
    index, text = select_culprit_chunk(chunks)
    assert index == 2
    assert text == "gamma"


def test_select_culprit_empty_raises():
    with pytest.raises(ValueError):
        select_culprit_chunk([])


def test_score_weights_strong_signals_higher():
    assert _score_chunk("traceback") > _score_chunk("error")
