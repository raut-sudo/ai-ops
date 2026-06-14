from __future__ import annotations

from app.graph.hitl_utils import _is_awaiting_hitl


class _Snapshot:
    def __init__(self, next_nodes):
        self.next = next_nodes


def test_is_awaiting_hitl_true_when_reflection_in_next() -> None:
    """Graph paused inside reflection (HITL via interrupt())."""
    snapshot = _Snapshot(("reflection",))
    assert _is_awaiting_hitl(snapshot) is True


def test_is_awaiting_hitl_false_when_completed_next_empty() -> None:
    snapshot = _Snapshot(())
    assert _is_awaiting_hitl(snapshot) is False


def test_is_awaiting_hitl_false_when_other_nodes_present() -> None:
    snapshot = _Snapshot(("synthesizer", "aggregator"))
    assert _is_awaiting_hitl(snapshot) is False
