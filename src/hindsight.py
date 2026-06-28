# src/hindsight.py
"""Lightweight Hindsight memory manager.

Reads and writes past fixes to a local JSON file so the agent can recall
solutions to errors it has seen before. Both recall and retain derive their
key from the same `signature_for` helper, guaranteeing that a retained fix is
recallable for the identical error next time.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from .config import get_settings

# Cascade inserts a "--- LOG CHUNK N ---" header into each chunk. It is metadata,
# not error content, so it must be stripped before deriving a memory key;
# otherwise the same error in a different chunk position would not match.
_CHUNK_HEADER_RE = re.compile(r"---\s*LOG CHUNK\s*\d+\s*---")


def _db_file() -> str:
    return get_settings().hindsight_path


def _clean(text: str) -> str:
    """Remove chunk headers, BOM, and collapse whitespace."""
    text = (text or "").replace("\ufeff", "")
    text = _CHUNK_HEADER_RE.sub(" ", text)
    return " ".join(text.split())


def signature_for(chunk: str) -> str:
    """Derive the stable memory key for an error chunk.

    Strips Cascade chunk headers and BOM, normalizes whitespace, and trims to a
    bounded length so cosmetically different but substantively identical errors
    map to the same key. Used identically by recall and retain.
    """
    return _clean(chunk)[:100].strip()


def _load_memory() -> dict:
    path = _db_file()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save_memory(data: dict) -> None:
    with open(_db_file(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def recall_historical_fix(error_chunk: str) -> Optional[str]:
    """Recall a past fix for an error chunk, or None if unseen.

    Matches when a stored signature is contained within the (normalized)
    incoming chunk, so a stored 100-char signature still matches the full
    error text on a repeat failure.
    """
    memory = _load_memory()
    needle = _clean(error_chunk).lower()
    for known_signature, fix in memory.items():
        if known_signature.lower() in needle:
            return fix
    return None


def retain_successful_fix(error_chunk: str, suggested_fix: str) -> str:
    """Persist a fix keyed by the chunk's stable signature. Returns the key."""
    memory = _load_memory()
    key = signature_for(error_chunk)
    if key:
        memory[key] = suggested_fix
        _save_memory(memory)
    return key


def list_entries() -> dict:
    """Return all stored signature → fix entries."""
    return _load_memory()


def delete_entry(key: str) -> bool:
    """Delete one entry by key. Returns True if it existed."""
    memory = _load_memory()
    if key in memory:
        del memory[key]
        _save_memory(memory)
        return True
    return False
