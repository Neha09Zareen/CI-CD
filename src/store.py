# src/store.py
"""Durable, restart-surviving store for failure records.

JSON-file backed and guarded by a lock so concurrent background tasks and API
reads stay consistent. Records are kept in memory and flushed to disk on every
write. The interface hides the backend so it can be swapped for SQLite later.
"""

from __future__ import annotations

import json
import os
import threading

from .config import get_settings
from .models import FailureRecord


class FailureStore:
    """In-memory map of run_id -> FailureRecord, persisted to a JSON file."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or get_settings().store_path
        self._lock = threading.RLock()
        self._records: dict[int, FailureRecord] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for run_id, data in raw.items():
            try:
                self._records[int(run_id)] = FailureRecord(**data)
            except Exception:  # noqa: BLE001 - skip any corrupt record
                continue

    def _flush(self) -> None:
        serializable = {
            str(run_id): json.loads(record.model_dump_json())
            for run_id, record in self._records.items()
        }
        tmp = f"{self._path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)
        os.replace(tmp, self._path)

    def upsert(self, record: FailureRecord) -> None:
        with self._lock:
            self._records[record.run_id] = record
            self._flush()

    def get(self, run_id: int) -> FailureRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def list(self) -> list[FailureRecord]:
        """Return all records, newest first by detection time."""
        with self._lock:
            return sorted(
                self._records.values(),
                key=lambda r: r.detected_at,
                reverse=True,
            )

    def stats(self) -> dict:
        with self._lock:
            records = list(self._records.values())
        return {
            "total_failures": len(records),
            "memory_hits": sum(1 for r in records if r.source == "memory"),
            "generated_fixes": sum(1 for r in records if r.source == "generated"),
            "approved": sum(1 for r in records if r.status == "approved"),
            "rejected": sum(1 for r in records if r.status == "rejected"),
            "errors": sum(1 for r in records if r.status == "error"),
        }


# Module-level singleton shared across the app.
store = FailureStore()
