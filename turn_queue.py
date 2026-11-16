"""In-memory active turn and FIFO pending queue manager for voice/text chat."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple


class TurnQueueManager:
    """Thread-safe coordinator ensuring at most one active brain turn and an inspectable FIFO queue."""

    def __init__(self, stale_timeout_sec: float = 120.0) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stale_timeout_sec = stale_timeout_sec
        self._active_turn: Optional[dict] = None
        self._pending_queue: List[dict] = []

    def _make_turn_record(
        self,
        query: str,
        cancel_event: threading.Event,
        *,
        level: Optional[str] = None,
        client: str = "ios-voice",
        status: str = "queued",
    ) -> dict:
        now = time.time()
        return {
            "turn_id": uuid.uuid4().hex[:12],
            "timestamp": datetime.now().isoformat(),
            "query": query,
            "thought": "",
            "tools": [],
            "reply": "",
            "status": status,
            "level": level,
            "client": client,
            "updated_at": now,
            "cancel_event": cancel_event,
        }

    def enqueue_or_activate(
        self,
        query: str,
        cancel_event: threading.Event,
        *,
        level: Optional[str] = None,
        client: str = "ios-voice",
    ) -> Tuple[str, bool, int]:
        """Register a turn. Activates immediately if idle; otherwise appends to pending queue.

        Returns (turn_id, is_active_now, queue_position_1_based_or_0).
        """
        with self._cond:
            if self._active_turn is None and not self._pending_queue:
                rec = self._make_turn_record(
                    query, cancel_event, level=level, client=client, status="thinking"
                )
                self._active_turn = rec
                return rec["turn_id"], True, 0

            rec = self._make_turn_record(
                query, cancel_event, level=level, client=client, status="queued"
            )
            self._pending_queue.append(rec)
            pos = len(self._pending_queue)
            return rec["turn_id"], False, pos

    def wait_until_active_or_cancelled(
        self,
        turn_id: str,
        cancel_event: threading.Event,
        poll_interval: float = 0.25,
    ) -> bool:
        """Block a queued turn worker until it becomes active or is cancelled."""
        with self._cond:
            while True:
                if cancel_event.is_set():
                    self._pending_queue = [
                        item for item in self._pending_queue if item["turn_id"] != turn_id
                    ]
                    if self._active_turn and self._active_turn["turn_id"] == turn_id:
                        self._active_turn = None
                        self._promote_next_locked()
                    return False

                if self._active_turn and self._active_turn["turn_id"] == turn_id:
                    return True

                # Defensive recovery: if active slot became empty without promotion
                if self._active_turn is None:
                    self._promote_next_locked()
                    if self._active_turn and self._active_turn["turn_id"] == turn_id:
                        return True

                # If somehow removed from both active and pending
                still_queued = any(item["turn_id"] == turn_id for item in self._pending_queue)
                if not still_queued:
                    return False

                self._cond.wait(timeout=poll_interval)

    def _promote_next_locked(self) -> Optional[dict]:
        """Promote the first non-cancelled queued turn to active. Caller must hold _cond."""
        while self._pending_queue:
            nxt = self._pending_queue.pop(0)
            if nxt["cancel_event"].is_set():
                continue
            nxt["status"] = "thinking"
            nxt["updated_at"] = time.time()
            self._active_turn = nxt
            self._cond.notify_all()
            return nxt
        self._cond.notify_all()
        return None

    def update_active(
        self,
        turn_id: str,
        *,
        thought_delta: Optional[str] = None,
        tool_status: Optional[str] = None,
        text_delta: Optional[str] = None,
    ) -> None:
        with self._cond:
            if not self._active_turn or self._active_turn.get("turn_id") != turn_id:
                return
            if thought_delta:
                self._active_turn["thought"] += thought_delta
            if tool_status:
                tools = self._active_turn["tools"]
                if not tools or tools[-1] != tool_status:
                    tools.append(tool_status)
            if text_delta:
                self._active_turn["reply"] += text_delta
                self._active_turn["status"] = "streaming"
            self._active_turn["updated_at"] = time.time()

    def finish_active(self, turn_id: Optional[str] = None) -> Optional[dict]:
        """Clear the active turn (if matching turn_id) and promote the next queued turn."""
        with self._cond:
            if not self._active_turn:
                return None
            if turn_id is not None and self._active_turn.get("turn_id") != turn_id:
                return None
            prev = dict(self._active_turn)
            self._active_turn = None
            self._promote_next_locked()
            return prev

    def get_active_snapshot(
        self,
        on_stale: Optional[Callable[[dict], None]] = None,
    ) -> Optional[dict]:
        stale_snapshot = None
        result = None
        with self._cond:
            if self._active_turn:
                if time.time() - self._active_turn.get("updated_at", 0) > self._stale_timeout_sec:
                    stale_snapshot = dict(self._active_turn)
                    self._active_turn = None
                    self._promote_next_locked()
                else:
                    result = {
                        "turn_id": self._active_turn["turn_id"],
                        "timestamp": self._active_turn["timestamp"],
                        "query": self._active_turn["query"],
                        "thought": self._active_turn["thought"],
                        "tools": list(self._active_turn["tools"]),
                        "reply": self._active_turn["reply"],
                        "status": self._active_turn["status"],
                    }
        if stale_snapshot and on_stale:
            on_stale(stale_snapshot)
        return result

    def get_pending_snapshot(self) -> List[dict]:
        with self._cond:
            return [
                {
                    "turn_id": item["turn_id"],
                    "timestamp": item["timestamp"],
                    "query": item["query"],
                    "status": item["status"],
                    "level": item.get("level"),
                    "client": item.get("client"),
                }
                for item in self._pending_queue
                if not item["cancel_event"].is_set()
            ]

    def cancel_active(self) -> Optional[str]:
        """Signal cancellation only on the currently active turn. Returns its turn_id if any."""
        with self._cond:
            if not self._active_turn:
                return None
            self._active_turn["cancel_event"].set()
            self._cond.notify_all()
            return self._active_turn["turn_id"]

    def remove_turn(self, turn_id: str) -> str:
        """Remove a queued turn or signal cancellation on the active turn.

        Returns one of: 'removed_queued', 'cancelling_active', 'not_found'.
        """
        with self._cond:
            if self._active_turn and self._active_turn["turn_id"] == turn_id:
                self._active_turn["cancel_event"].set()
                self._cond.notify_all()
                return "cancelling_active"

            for idx, item in enumerate(self._pending_queue):
                if item["turn_id"] == turn_id:
                    item["cancel_event"].set()
                    self._pending_queue.pop(idx)
                    self._cond.notify_all()
                    return "removed_queued"

            return "not_found"

    def clear_pending_queue(self) -> int:
        """Cancel and remove all pending turns without touching the active turn."""
        with self._cond:
            count = len(self._pending_queue)
            for item in self._pending_queue:
                item["cancel_event"].set()
            self._pending_queue.clear()
            self._cond.notify_all()
            return count

    def get_all_cancel_events(self) -> List[threading.Event]:
        with self._cond:
            events: List[threading.Event] = []
            if self._active_turn:
                events.append(self._active_turn["cancel_event"])
            for item in self._pending_queue:
                events.append(item["cancel_event"])
            return events
