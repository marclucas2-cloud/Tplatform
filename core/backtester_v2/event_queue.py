"""Priority event queue sorted by timestamp.

Uses heapq for O(log n) insert and O(log n) pop. Ties are broken
by insertion order to guarantee deterministic replay.
"""

from __future__ import annotations

import heapq
from typing import List

from core.backtester_v2.types import Event


class EventQueue:
    """Min-heap priority queue of Events, ordered by timestamp.

    Attributes:
        _heap: Internal heap storage as (timestamp_ns, seq, event) tuples.
        _seq: Monotonic counter for stable tie-breaking.
    """

    def __init__(self) -> None:
        self._heap: List[tuple[int, int, Event]] = []
        self._seq: int = 0

    def push(self, event: Event) -> None:
        """Insert an event into the queue.

        Args:
            event: The event to enqueue.
        """
        ts_ns = int(event.timestamp.value)
        heapq.heappush(self._heap, (ts_ns, self._seq, event))
        self._seq += 1

    def pop(self) -> Event:
        """Remove and return the earliest event.

        Returns:
            The event with the smallest timestamp.

        Raises:
            IndexError: If the queue is empty.
        """
        if not self._heap:
            raise IndexError("pop from empty EventQueue")
        _, _, event = heapq.heappop(self._heap)
        return event

    def peek(self) -> Event | None:
        """Return the earliest event without removing it.

        Returns:
            The next event, or None if the queue is empty.
        """
        if not self._heap:
            return None
        return self._heap[0][2]

    def is_empty(self) -> bool:
        """Check if the queue has no events."""
        return len(self._heap) == 0

    def __len__(self) -> int:
        return len(self._heap)

    def __repr__(self) -> str:
        return f"EventQueue(len={len(self)})"
