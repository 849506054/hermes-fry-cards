"""Final tool totals are available in each display mode."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from hermes_fry_cards.cardkit.builder import body_content_elements, build_complete_card
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.streaming.segments import SegmentState
from hermes_fry_cards.streaming.session import CardSession, SessionState
from hermes_fry_cards.streaming.tooluse import ToolUseTracker


@pytest.mark.parametrize("reasoning,tools", [(True, False), (False, True), (True, True)])
def test_final_totals_shared_by_modes(reasoning, tools):
    state = SegmentState()
    state.on_reasoning_delta("thought")
    state.on_tool_event(1)
    state.on_completion_answer("FINAL")
    card = build_complete_card(segments=state.segments, all_tool_steps=[],
                               process_messages=["Visible progress"],
                               show_reasoning=reasoning, show_tool_use=tools,
                               reasoning_count=1, tool_count=3, total_tool_elapsed_ms=3500)
    panel = next(e for e in body_content_elements(card) if e["tag"] == "collapsible_panel")
    # 计数搬到框内首行，框头只剩配置里的字段（默认 fields 下无数据即落 🍟）。
    assert "💭" not in panel["header"]["title"]["i18n_content"]["zh_cn"]
    assert panel["elements"][0]["tag"] == "markdown"
    assert "💭 思考 1 轮 · 工具执行 3 步" in panel["elements"][0]["i18n_content"]["zh_cn"]
    assert [e["content"] for e in panel["elements"][1:]] == ["Visible progress"]
    assert panel["expanded"] is False
    assert panel["header"]["position"] == "top"
    assert "tool_totals" not in str(card)


def test_tool_duration_sums_execution_including_unfinished_call():
    tracker = ToolUseTracker()
    with patch("hermes_fry_cards.streaming.tooluse.time.time", return_value=100):
        tracker.record_start("first")
    with patch("hermes_fry_cards.streaming.tooluse.time.time", return_value=102):
        tracker.record_end("first")
    with patch("hermes_fry_cards.streaming.tooluse.time.time", return_value=200):
        tracker.record_start("second")
    with patch("hermes_fry_cards.streaming.tooluse.time.time", return_value=201.5):
        assert tracker.execution_elapsed_ms == 3500
        tracker.record_end("second", error="stopped")
    with patch("hermes_fry_cards.streaming.tooluse.time.time", return_value=500):
        assert tracker.execution_elapsed_ms == 3500


@pytest.mark.asyncio
async def test_clarify_preserves_tool_duration_with_count():
    ctrl = StreamCardController()
    ctrl._client = AsyncMock()
    ctrl._client.cardkit_create.return_value = "new-card"
    ctrl._client.reply_card_by_id.return_value = "new-msg"
    session = CardSession("summary-fixture", "fixture-chat", asyncio.get_running_loop())
    session.set_card(card_id="card", card_msg_id="msg")
    session.state = SessionState.STREAMING
    with patch("hermes_fry_cards.streaming.tooluse.time.time", return_value=100):
        session.tool_use.record_start("first")
    with patch("hermes_fry_cards.streaming.tooluse.time.time", return_value=102):
        session.tool_use.record_end("first")
    session.segment_state.on_tool_event(1)
    assert await ctrl._do_clarify_split(session)
    assert session.process_counts[1] == 1
    assert session.total_tool_elapsed_ms == 2000
    assert await ctrl._do_clarify_split(session)
    assert session.total_tool_elapsed_ms == 2000
