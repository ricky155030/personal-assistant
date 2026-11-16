"""Unit tests for TurnQueueManager (active turn + inspectable FIFO pending queue)."""

import threading
import time

from turn_queue import TurnQueueManager


def test_single_turn_activates_immediately_and_finishes():
    mgr = TurnQueueManager()
    ev1 = threading.Event()
    turn_id, is_active, pos = mgr.enqueue_or_activate("查待買清單", ev1)

    assert is_active is True
    assert pos == 0
    assert mgr.get_active_snapshot()["turn_id"] == turn_id
    assert mgr.get_pending_snapshot() == []

    mgr.update_active(turn_id, thought_delta="思考中", tool_status="查詢試算表", text_delta="共28項")
    snap = mgr.get_active_snapshot()
    assert snap["status"] == "streaming"
    assert snap["reply"] == "共28項"
    assert snap["tools"] == ["查詢試算表"]

    mgr.finish_active(turn_id)
    assert mgr.get_active_snapshot() is None
    assert mgr.get_pending_snapshot() == []


def test_multiple_turns_queue_without_overwriting_active():
    mgr = TurnQueueManager()
    ev1, ev2, ev3 = threading.Event(), threading.Event(), threading.Event()

    id1, active1, pos1 = mgr.enqueue_or_activate("第一題", ev1)
    id2, active2, pos2 = mgr.enqueue_or_activate("第二題", ev2)
    id3, active3, pos3 = mgr.enqueue_or_activate("第三題", ev3)

    assert active1 is True and pos1 == 0
    assert active2 is False and pos2 == 1
    assert active3 is False and pos3 == 2

    # Active turn must NOT be overwritten by later enqueues
    active_snap = mgr.get_active_snapshot()
    assert active_snap["turn_id"] == id1
    assert active_snap["query"] == "第一題"

    pending = mgr.get_pending_snapshot()
    assert [p["turn_id"] for p in pending] == [id2, id3]
    assert [p["query"] for p in pending] == ["第二題", "第三題"]

    # Finish 1 -> automatically promotes 2
    mgr.finish_active(id1)
    assert mgr.get_active_snapshot()["turn_id"] == id2
    assert [p["turn_id"] for p in mgr.get_pending_snapshot()] == [id3]

    # Finish 2 -> automatically promotes 3
    mgr.finish_active(id2)
    assert mgr.get_active_snapshot()["turn_id"] == id3
    assert mgr.get_pending_snapshot() == []

    mgr.finish_active(id3)
    assert mgr.get_active_snapshot() is None


def test_remove_queued_turn_skips_it_and_wakes_waiter():
    mgr = TurnQueueManager()
    ev1, ev2, ev3 = threading.Event(), threading.Event(), threading.Event()

    id1, _, _ = mgr.enqueue_or_activate("第一題", ev1)
    id2, _, _ = mgr.enqueue_or_activate("第二題(要刪除)", ev2)
    id3, _, _ = mgr.enqueue_or_activate("第三題", ev3)

    waiter2_result = []

    def wait_t2():
        waiter2_result.append(mgr.wait_until_active_or_cancelled(id2, ev2, poll_interval=0.05))

    t2 = threading.Thread(target=wait_t2)
    t2.start()

    res = mgr.remove_turn(id2)
    assert res == "removed_queued"
    assert ev2.is_set()
    t2.join(timeout=1.0)
    assert waiter2_result == [False]

    # Pending queue should only contain id3
    assert [p["turn_id"] for p in mgr.get_pending_snapshot()] == [id3]

    # Finishing id1 promotes id3 directly
    mgr.finish_active(id1)
    assert mgr.get_active_snapshot()["turn_id"] == id3
    mgr.finish_active(id3)


def test_cancel_active_leaves_pending_queue_intact():
    mgr = TurnQueueManager()
    ev1, ev2 = threading.Event(), threading.Event()

    id1, _, _ = mgr.enqueue_or_activate("卡住的任務", ev1)
    id2, _, _ = mgr.enqueue_or_activate("排隊中的任務", ev2)

    cancelled_id = mgr.cancel_active()
    assert cancelled_id == id1
    assert ev1.is_set()
    assert not ev2.is_set()
    assert [p["turn_id"] for p in mgr.get_pending_snapshot()] == [id2]


def test_clear_pending_queue_only_clears_pending():
    mgr = TurnQueueManager()
    ev1, ev2, ev3 = threading.Event(), threading.Event(), threading.Event()

    id1, _, _ = mgr.enqueue_or_activate("進行中", ev1)
    id2, _, _ = mgr.enqueue_or_activate("排隊1", ev2)
    id3, _, _ = mgr.enqueue_or_activate("排隊2", ev3)

    cleared = mgr.clear_pending_queue()
    assert cleared == 2
    assert not ev1.is_set()
    assert ev2.is_set() and ev3.is_set()
    assert mgr.get_active_snapshot()["turn_id"] == id1
    assert mgr.get_pending_snapshot() == []


def test_stale_active_turn_promotes_next():
    mgr = TurnQueueManager(stale_timeout_sec=0.05)
    ev1, ev2 = threading.Event(), threading.Event()

    id1, _, _ = mgr.enqueue_or_activate("逾時任務", ev1)
    id2, _, _ = mgr.enqueue_or_activate("下一棒", ev2)

    time.sleep(0.08)
    stale_seen = []
    snap = mgr.get_active_snapshot(on_stale=lambda s: stale_seen.append(s["turn_id"]))

    assert stale_seen == [id1]
    assert snap is None
    # Next turn should have been promoted!
    next_snap = mgr.get_active_snapshot()
    assert next_snap is not None
    assert next_snap["turn_id"] == id2
