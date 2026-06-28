# src/hindsight.py
"""Lightweight Hindsight memory manager.

Reads and writes past fixes to a local JSON file so the agent can recall
solutions to errors it has seen before.
"""

import os
import json
from typing import Optional

DB_FILE = "hindsight_db.json"


def _load_memory() -> dict:
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save_memory(data: dict):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)


def recall_historical_fix(error_keyword: str) -> Optional[str]:
    """Mandatory Hindsight Requirement: Recall past fixes.

    Searches the JSON memory for a known error signature.
    """
    memory = _load_memory()
    # Scrappy keyword matching for the hackathon MVP
    for known_error, fix in memory.items():
        if known_error.lower() in error_keyword.lower():
            return fix
    return None


def retain_successful_fix(error_signature: str, suggested_fix: str):
    """Mandatory Hindsight Requirement: Retain new fixes.

    Saves the generated fix to long-term memory.
    """
    memory = _load_memory()
    # Use a snippet of the error as the key
    key = error_signature[:100].strip()
    memory[key] = suggested_fix
    _save_memory(memory)
    print(f"💾 Hindsight: Retained fix for error signature: {key[:30]}...")
