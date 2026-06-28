# src/events.py
"""In-process async event bus for Server-Sent Events (SSE) fan-out.

The pipeline publishes progress events; every connected SSE client has its own
bounded queue and receives a copy. Publishing is non-blocking: if a client's
queue is full (slow or stalled consumer), the event is dropped for that client
rather than blocking pipeline progress.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

# Per-subscriber buffer size. Enough to absorb bursts without unbounded growth.
_QUEUE_MAXSIZE = 1000


class EventBus:
    """Async publish/subscribe hub with one queue per subscriber."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> "asyncio.Queue[str]":
        """Register a new subscriber and return its queue."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[str]") -> None:
        """Remove a subscriber's queue."""
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        """Serialize and fan out an event to all subscribers (non-blocking).

        Safe to call from synchronous code. Drops the event for any subscriber
        whose buffer is full so a slow client never stalls the pipeline.
        """
        payload = json.dumps(event, default=str)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow consumer; drop this event for them rather than block.
                continue

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Module-level singleton shared across the app.
bus = EventBus()
