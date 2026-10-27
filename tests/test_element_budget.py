"""Issue #41 — 元素记账回归.

四处缺陷都在 streaming/controller.py 的共享工具面板记账上：

1. 面板增量恒为 0：``_do_flush`` 提前推进 ``session.tool_panel_estimate``，
   ``_do_batch_update`` 再取「批更新前」的值时已是新值 → 增量 0，面板元素
   从不进 ``element_count``，``ELEMENT_THRESHOLD`` 主动拆卡一次也不触发。
2. 拆卡后新卡重发旧卡步骤：dirty 分支写死 ``start = 0``。
3. 强制拆卡（300305）不推进 ``split_index``：完成态按 ``active_segments()``
   重建，把旧卡内容再渲染一遍，再次撞上限。
4. 拆卡判断把面板重复计入：``_maybe_rollover_tool_segment`` 以恒为 0 的
   ``seg.element_estimate`` 为增量基准。

用例是状态级的，不触达飞书接口真实上限。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from test_controller import _make_session, _setup_ctrl

from hermes_fry_cards.feishu import FeishuAPIError
from hermes_fry_cards.streaming.segment_helper import (
    ELEMENT_THRESHOLD,
    estimate_tool_elements,
    panel_steps_start,
)
from hermes_fry_cards.streaming.session import SessionState


def _streaming_session(ctrl, msg_id: str, card_id: str):
    session = _make_session(msg_id)
    session.state = SessionState.STREAMING
    session.card_id = card_id
    session.card_msg_id = f"card_msg_{card_id}"
    ctrl._sessions[msg_id] = session
    return session


def _add_steps(session, prefix: str, count: int) -> None:
    for idx in range(count):
        session.tool_use.record_start("read", f"{prefix}{idx}")
        session.segment_state.on_tool_event(len(session.tool_use.build_display_steps()))


async def _flush(ctrl, session) -> None:
    await ctrl._do_flush(session)


def _last_panel_action(client, card_id: str | None = None) -> dict | None:
    """最后一次（可限定卡）批更新中针对共享面板的 action."""
    for call in reversed(client.cardkit_batch_update.call_args_list):
        if card_id is not None and call.args[0] != card_id:
            continue
        for action in call.args[1]:
            if isinstance(action, dict) and action.get("params", {}).get("element_id") == "tool_panel":
                return action
    return None


def _panel_signature(action: dict) -> str:
    return repr(action["params"]["partial_element"]["elements"])


@pytest.mark.asyncio
async def test_panel_elements_are_counted_on_every_flush() -> None:
    """连续工具步骤必须严格推进 element_count，且增量等于面板估算增量."""
    ctrl = _setup_ctrl()
    session = _streaming_session(ctrl, "msg_count", "card_count")
    session.element_count = 1

    previous_count = session.element_count
    previous_panel = 0
    for idx in range(1, 6):
        _add_steps(session, f"file{idx}_", 1)
        await _flush(ctrl, session)

        steps = session.tool_use.build_display_steps()
        expected_panel = estimate_tool_elements(0, len(steps), steps)
        assert session.tool_panel_estimate == expected_panel
        assert session.element_count == previous_count + (expected_panel - previous_panel), (
            f"step {idx}: panel delta not counted"
        )
        previous_count = session.element_count
        previous_panel = expected_panel

    ctrl._client.cardkit_create.assert_not_called()
    assert session.split_index == 0


@pytest.mark.asyncio
async def test_new_card_panel_does_not_repaint_old_steps() -> None:
    """拆卡后新卡的面板只渲染本卡步骤，不重发旧卡步骤."""
    ctrl = _setup_ctrl()
    client = ctrl._client
    client.cardkit_create = AsyncMock(return_value="card_second")
    client.reply_card_by_id = AsyncMock(return_value="msg_second")
    session = _streaming_session(ctrl, "msg_repaint", "card_first")

    session.element_count = 1
    _add_steps(session, "old_", 3)
    await _flush(ctrl, session)
    old_panel = _last_panel_action(client, "card_first")
    assert old_panel is not None

    # 手动切卡：新卡承接 answer 之后的工具段
    session.segment_state.on_answer_delta("mid")
    assert await ctrl._do_split_card(
        session, len(session.segment_state.segments), [], set(), {}, [],
    ) is True
    assert session.card_id == "card_second"

    _add_steps(session, "new_", 2)
    await _flush(ctrl, session)

    new_panel = _last_panel_action(client, "card_second")
    assert new_panel is not None, "新卡必须重建面板"
    first_tool = next(s for s in session.segment_state.segments[session.split_index:] if s.type == "tool")
    assert first_tool.tool_offset > 0
    assert _panel_signature(new_panel) != _panel_signature(old_panel), (
        "new card panel must start at its own first tool step"
    )


def test_panel_start_uses_first_tool_segment_of_card() -> None:
    """本卡面板起点取本卡首个工具段，而不是所有工具段的最小 offset."""
    from hermes_fry_cards.streaming.segments import Segment

    first = Segment("answer", "a1")
    tool_a = Segment("tool", "tools_1")
    tool_a.tool_offset = 0
    tool_b = Segment("tool", "tools_2")
    # 同一张卡上后续打开的段 offset 可能小于前面已入账段的 end_offset
    tool_b.tool_offset = 2
    later = Segment("tool", "tools_3")
    later.tool_offset = 1

    segments = [first, tool_a, tool_b, later]
    assert panel_steps_start(segments, 0, 0) == 0
    assert panel_steps_start(segments, 3, 1) == 1
    assert panel_steps_start([first], 0, 7) == 7


def _panel_head_detail(action: dict) -> str:
    """面板 payload 里第一个步骤的 detail（= 面板第 0 个渲染步骤）."""
    for element in action["params"]["partial_element"]["elements"]:
        text = element.get("text") or {}
        if text.get("tag") == "plain_text":
            return text.get("content") or ""
    return ""


@pytest.mark.asyncio
async def test_tool_segment_edited_after_split_does_not_repaint_old_card_steps() -> None:
    """拆卡后更新本卡工具段时，面板渲染区间从本卡首个工具段起，而非全量重渲."""
    ctrl = _setup_ctrl()
    client = ctrl._client
    client.cardkit_create = AsyncMock(return_value="card_second")
    client.reply_card_by_id = AsyncMock(return_value="msg_second")
    session = _streaming_session(ctrl, "msg_repaint", "card_first")

    session.element_count = 1
    _add_steps(session, "old_", 3)
    await _flush(ctrl, session)
    assert _panel_head_detail(_last_panel_action(client, "card_first") or {}) == "old_0"

    # 切卡：新卡承接后续工具段（已有工具段被移到 split_index 之前）
    session.segment_state.on_answer_delta("mid")
    assert await ctrl._do_split_card(
        session, len(session.segment_state.segments), [], set(), {}, [],
    ) is True
    assert session.split_index == 2

    _add_steps(session, "new_", 2)
    await _flush(ctrl, session)
    # 新卡上的工具段随后再有变化，触发 dirty 更新分支
    _add_steps(session, "new_", 1)
    await _flush(ctrl, session)

    new_panel = _last_panel_action(client, "card_second")
    assert new_panel is not None
    head = _panel_head_detail(new_panel)
    assert head == "new_0", f"new card panel repainted old steps: {head!r}"
    new_steps = session.tool_use.build_display_steps()
    assert session.tool_panel_estimate == estimate_tool_elements(
        3, len(new_steps), new_steps,
    )


@pytest.mark.asyncio
async def test_force_split_advances_split_index() -> None:
    """300305 强制拆卡后，split_index 指向首个未创建 segment."""
    ctrl = _setup_ctrl()
    session = _streaming_session(ctrl, "msg_force", "card_force_old")

    # 已创建段（封印在旧卡上）+ 未创建段（迁到新卡）。切到 answer 段后，
    # ``segments`` 中 answer 会被追加，断言因此按引用与下标比，而不是按相等。
    session.segment_state.on_reasoning_delta("sealed")
    sealed = session.segment_state.segments[0]
    sealed.created = True
    sealed.dirty = False
    session.segment_state.on_answer_delta("pending answer")
    pending = session.segment_state.segments[-1]
    assert not pending.created
    session.element_count = ELEMENT_THRESHOLD

    await ctrl._force_split_on_element_limit(session)

    segments = session.segment_state.segments
    assert session.card_id != "card_force_old"
    assert session.split_index == segments.index(pending)
    active = session.active_segments()
    assert not any(seg is sealed for seg in active), (
        "已封印在旧卡上的 segment 不得出现在完成态重建里"
    )
    assert pending in active
    assert pending.dirty


@pytest.mark.asyncio
async def test_force_split_triggered_by_300305_in_flush() -> None:
    """批更新撞 300305 时自动强制拆卡，并把 split_index 推过已创建段."""
    ctrl = _setup_ctrl()
    client = ctrl._client
    session = _streaming_session(ctrl, "msg_300305", "card_limit")
    session.segment_state.on_reasoning_delta("old")
    session.segment_state.segments[0].created = True
    session.segment_state.segments[0].dirty = False
    session.segment_state.on_answer_delta("new")
    # 第二次批更新失败（首次成功，已建新卡）
    client.cardkit_batch_update = AsyncMock(
        side_effect=[
            None,
            FeishuAPIError("cardkit_batch_update: code=300305 total limit", 300305),
        ]
    )
    session.element_count = ELEMENT_THRESHOLD

    await _flush(ctrl, session)

    assert session.card_id != "card_limit"
    assert session.split_index == 1
    assert [seg.type.value for seg in session.active_segments()] == ["answer"]


@pytest.mark.asyncio
async def test_rollover_stays_flat_after_panel_cap() -> None:
    """面板截断后单卡元素数有界：纯工具轮次跑到 40 步也不拆卡（真实计数口径）。

    回归样本 2026-10-27 22:00 轮：54 步没拆卡，账面 156、真实面板只有 94 格。
    旧用例按「每步 7 格」的常数口径写，二十多步就误判为接近上限。
    """
    ctrl = _setup_ctrl()
    client = ctrl._client
    client.cardkit_create = AsyncMock(return_value="card_next")
    client.reply_card_by_id = AsyncMock(return_value="msg_next")
    session = _streaming_session(ctrl, "msg_roll", "card_roll")
    session.element_count = 1

    counts = []
    for idx in range(1, 41):
        session.tool_use.record_start("exec", f"f{idx}_0")
        session.tool_use.record_end("exec", output="out" * 32)
        session.segment_state.on_tool_event(len(session.tool_use.build_display_steps()))
        await _flush(ctrl, session)
        counts.append(session.element_count)
        assert session.split_index == 0, f"面板封顶后不该拆卡: 第 {idx} 步拆了"

    assert max(counts) < ELEMENT_THRESHOLD, "面板封顶后纯工具轮次不应顶到拆卡阈值"
    assert counts[-1] <= counts[29] + 2, "第 30 步之后元素数应有界: 面板只留最近 30 步"


@pytest.mark.asyncio
async def test_completion_drops_summary_line_on_element_limit() -> None:
    """完成态撞 300305 时去掉「过程已收起」那一行再发，正文优先入卡。

    背景（2026-10-27 实测）：流式过 30 步的卡片，完成态整卡重建带面板必被
    300305 拒；面板子元素在飞书侧被重复计一次，本地账面看不出来。
    """
    import json as _json

    ctrl = _setup_ctrl()
    client = ctrl._client
    client.cardkit_close_streaming = AsyncMock()
    bodies: list[dict] = []

    async def update(card_id: str, card: dict, sequence: int = 0) -> None:
        bodies.append(card)
        if len(bodies) == 1:
            raise FeishuAPIError("element exceeds the limit", code=300305)

    client.cardkit_update = update
    session = _streaming_session(ctrl, "msg_slim", "card_slim")
    _add_steps(session, "f", 3)
    await _flush(ctrl, session)

    assert await ctrl._do_complete_card(session) is True
    assert len(bodies) == 2
    assert "过程已收起" in _json.dumps(bodies[0], ensure_ascii=False)
    assert "过程已收起" not in _json.dumps(bodies[1], ensure_ascii=False)
    assert len(bodies[1]["body"]["elements"]) < len(bodies[0]["body"]["elements"])


@pytest.mark.asyncio
async def test_overflow_fallback_advances_sequence() -> None:
    """降级卡替换不得复用删 loading 用掉的序号，否则回 300317、兜底也失效。"""
    from unittest.mock import patch

    ctrl = _setup_ctrl()
    client = ctrl._client
    client.cardkit_close_streaming = AsyncMock()
    client.cardkit_update = AsyncMock(side_effect=FeishuAPIError("limit", code=300305))
    batch_seqs: list[int] = []

    async def batch_update(card_id: str, actions: list, sequence: int = 0) -> None:
        batch_seqs.append(sequence)

    client.cardkit_batch_update = batch_update
    session = _streaming_session(ctrl, "msg_seq", "card_seq")
    session.sequence = 100

    with patch("asyncio.sleep", new_callable=AsyncMock):
        assert await ctrl._do_complete_card(session) is False

    update_seqs = [c.kwargs.get("sequence") for c in client.cardkit_update.await_args_list]
    assert batch_seqs, "删 loading 的 batch update 应该发生"
    assert update_seqs[-1] > max(batch_seqs), (update_seqs, batch_seqs)
    assert len(set(update_seqs)) == len(update_seqs), f"完成态序号重号: {update_seqs}"
