"""Agent memory store.

Provides a minimal interface for persisting and retrieving agent
conversation/state. Swap the in-memory backend for a database or the
Hindsight API as the project grows.
"""

from __future__ import annotations

from typing import Any


class Memory:
    """Simple in-memory key/value store for agent state."""

    def __init__(self) -> None:
        self._store: dict[str, list[Any]] = {}

    def append(self, session_id: str, item: Any) -> None:
        """Append an item to a session's history."""
        self._store.setdefault(session_id, []).append(item)

    def history(self, session_id: str) -> list[Any]:
        """Return the full history for a session."""
        return self._store.get(session_id, [])

    def clear(self, session_id: str) -> None:
        """Clear a session's history."""
        self._store.pop(session_id, None)
