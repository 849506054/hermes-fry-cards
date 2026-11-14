"""思考时间线滚动窗口回归 — 整卡体积（200860）的压体积落点.

飞书对整卡有载荷字节上限，超限返回 ``200860 card over max size``；撞上后
拆卡前的预拆 flush 会被拒、封卡也连败，整轮退回纯文本。这条线在流式期
按尾部窗口回收最早的思考段，把卡体积压在预算内。

用例覆盖：窗口函数的两道闸门、回收动作的元素 id 与提示行落点、回收后的
段状态（不再重建/不再写正文）。
"""
from __future__ import annotations

import pytest
from test_controller import _make_session, _setup_ctrl

from hermes_fry_cards.streaming.segments import SegmentType
from hermes_fry_cards.streaming.session import SessionState
from hermes_fry_cards.streaming.window import (
    REASONING_TRIM_NOTE_ELEMENT_ID,
    REASONING_WINDOW_BYTES,
    REASONING_WINDOW_ROUNDS,
    reasoning_evictions,
)


def _session(ctrl, msg_id: str = "msg_trim"):
    session = _make_session(msg_id)
    session.state = SessionState.STREAMING
    session.card_id = "card_trim"
    session.card_msg_id = "card_msg_trim"
    session.reasoning_timeline = True
    # 时间线模式下面板不设上限（面板上限那条路是把新文本并进最后一段，会掩盖回收）
    session.segment_state.max_reasoning_panels = None
    ctrl._sessions[msg_id] = session
    return session


def _add_reasoning(session, count: int, bytes_each: int = 1200) -> None:
    """写入 ``count`` 段已上卡的思考段（标题 + 正文各一个元素）.

    真实的思考段被工具调用分隔，所以每轮之间插一步工具事件；否则连续增量会被
    并进同一段（on_reasoning_delta 的合并语义）。
    """
    state = session.segment_state
    for index in range(count):
        if state.segments and state.segments[-1].type == SegmentType.REASONING:
            session.tool_use.record_start("read", f"probe{index}")
            state.on_tool_event(len(session.tool_use.build_display_steps()))
        state.on_reasoning_delta(f"第{index}段思考" * max(1, bytes_each // 18))
        seg = state.segments[-1]
        seg.created = True
        session.element_count += 2


class TestWindowFunction:
    def test_under_both_gates_evicts_nothing(self) -> None:
        assert reasoning_evictions([100] * REASONING_WINDOW_ROUNDS) == 0

    def test_round_gate_keeps_tail(self) -> None:
        assert reasoning_evictions([100] * (REASONING_WINDOW_ROUNDS + 4)) == 4

    def test_byte_gate_evicts_from_head(self) -> None:
        sizes = [REASONING_WINDOW_BYTES // 4] * 8
        evict = reasoning_evictions(sizes)
        assert evict == 4
        assert sum(sizes[evict:]) <= REASONING_WINDOW_BYTES

    def test_keeps_at_least_one_round(self) -> None:
        assert reasoning_evictions([REASONING_WINDOW_BYTES * 3]) == 0


@pytest.mark.asyncio
async def test_over_window_evicts_oldest_and_leaves_note() -> None:
    ctrl = _setup_ctrl()
    session = _session(ctrl)
    _add_reasoning(session, REASONING_WINDOW_ROUNDS + 3)
    segments = session.segment_state.segments
    dropped = [seg for seg in segments if seg.type == SegmentType.REASONING][:3]
    first_kept = [seg for seg in segments if seg.type == SegmentType.REASONING][3]

    await ctrl._trim_reasoning_window(session, session.segment_state)

    actions = ctrl._client.cardkit_batch_update.call_args.args[1]
    delete_action = next(a for a in actions if a["action"] == "delete_elements")
    assert delete_action["params"]["element_ids"] == [
        eid for seg in dropped for eid in (seg.el_id, seg.text_el_id)
    ]

    note_action = next(a for a in actions if a["action"] == "add_elements")
    assert note_action["params"]["target_element_id"] == first_kept.el_id
    note = note_action["params"]["elements"][0]
    assert note["element_id"] == REASONING_TRIM_NOTE_ELEMENT_ID
    assert "3" in note["content"]

    for seg in dropped:
        assert seg.evicted is True
        assert seg.created is False
    assert session.reasoning_trimmed == 3
    assert session.reasoning_trim_note_created is True


@pytest.mark.asyncio
async def test_second_trim_updates_note_instead_of_recreating_it() -> None:
    ctrl = _setup_ctrl()
    session = _session(ctrl)
    _add_reasoning(session, REASONING_WINDOW_ROUNDS + 3)
    await ctrl._trim_reasoning_window(session, session.segment_state)

    _add_reasoning(session, 2)
    await ctrl._trim_reasoning_window(session, session.segment_state)

    actions = ctrl._client.cardkit_batch_update.call_args.args[1]
    assert not [a for a in actions if a["action"] == "add_elements"]
    update = next(a for a in actions if a["action"] == "partial_update_element")
    assert update["params"]["element_id"] == REASONING_TRIM_NOTE_ELEMENT_ID
    assert "5" in update["params"]["partial_element"]["content"]


@pytest.mark.asyncio
async def test_evicted_segments_are_not_written_again() -> None:
    ctrl = _setup_ctrl()
    session = _session(ctrl)
    _add_reasoning(session, REASONING_WINDOW_ROUNDS + 3)
    await ctrl._trim_reasoning_window(session, session.segment_state)
    dropped = [seg for seg in session.segment_state.segments if seg.evicted]

    ctrl._client.cardkit_batch_update.reset_mock()
    dropped[0].dirty = True
    await ctrl._do_flush(session)

    added_ids = [
        action["params"].get("element_id")
        for call in ctrl._client.cardkit_batch_update.call_args_list
        for action in call.args[1]
        if action["action"] == "add_elements"
    ]
    assert dropped[0].el_id not in added_ids
