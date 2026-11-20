"""Compaction lines ride the running turn card; the blocked-context warning included."""
import pytest

from hermes_fry_cards import compaction_card, manual_compaction
from hermes_fry_cards import patch as fry_patch
from hermes_fry_cards.streaming.segments import SegmentState
from tests.test_system_messages import Result, adapter, marked_send  # noqa: F401  (shared fixture)

BLOCKED = ("⚠ Context is over the compression threshold (~55,183 tokens >= 40,000) "
           "but compression is currently blocked (ineffective). The model may stop responding. "
           "Run /new to start a fresh session or /compress to retry immediately.")


class _State:
    def __init__(self, terminal=False):
        self.is_terminal = terminal


class _Session:
    def __init__(self, message_id, terminal=False):
        self.message_id = message_id
        self.state = _State(terminal)
        self.segment_state = SegmentState()


class _Controller:
    def __init__(self, sessions):
        self._sessions = sessions
        self.notices = []

    def _schedule_flush(self, session):
        pass

    from hermes_fry_cards.controller import StreamCardController as _Real

    _resolve_session = _Real._resolve_session
    _notice_session = _Real._notice_session
    on_compaction_notice = _Real.on_compaction_notice


def test_blocked_warning_is_its_own_class():
    assert compaction_card.is_blocked_warning(BLOCKED)
    assert not compaction_card.is_blocked_warning("📦 Preflight compression: ~260,000 tokens >= 256,000 threshold.")
    assert not compaction_card.is_blocked_warning("✓ Context compaction complete — continuing turn...")
    assert not compaction_card.is_blocked_warning("")
    assert compaction_card.classify(BLOCKED) is None


@pytest.mark.asyncio
async def test_blocked_warning_merges_into_the_running_card(adapter, monkeypatch):  # noqa: F811
    session = _Session("om_card")
    ctrl = _Controller({"m1": session})
    monkeypatch.setattr(fry_patch, "get_controller", lambda: ctrl)
    result = await marked_send(adapter, "chat", BLOCKED)
    assert result.success
    assert [notice.text for notice in session.segment_state.notices] == [BLOCKED]
    adapter._feishu_send_with_retry.assert_not_awaited()
    adapter.plain.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_warning_keeps_its_own_card_when_no_turn_runs(adapter, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fry_patch, "get_controller", lambda: None)
    result = await marked_send(adapter, "chat", BLOCKED)
    assert result.success
    assert adapter._feishu_send_with_retry.await_count == 1


@pytest.mark.asyncio
async def test_blocked_warning_never_merges_while_manual_compress_runs(adapter, monkeypatch):  # noqa: F811
    ctrl = _Controller({"m1": _Session("om_card")})
    monkeypatch.setattr(fry_patch, "get_controller", lambda: ctrl)
    token = manual_compaction._manual_lines.set(True)
    try:
        await marked_send(adapter, "chat", BLOCKED)
    finally:
        manual_compaction._manual_lines.reset(token)
    assert not ctrl._sessions["m1"].segment_state.notices


def test_merge_needs_exactly_one_running_card():
    one = _Controller({"m1": _Session("om_card")})
    assert one.on_compaction_notice(text=BLOCKED)
    assert _Controller({}).on_compaction_notice(text=BLOCKED) is False
    two = _Controller({"m1": _Session("a"), "m2": _Session("b")})
    assert two.on_compaction_notice(text=BLOCKED) is False
    closed = _Controller({"m1": _Session("c", terminal=True)})
    assert closed.on_compaction_notice(text=BLOCKED) is False
