"""元素预算回归：工具面板的嵌套子元素必须计入 element_count.

2026-10-20 22:58 实测（卡 769875792784）：59 次工具面板更新后 `elements=3`（追踪值），
卡片实际已顶到飞书 200 元素上限，加一个 answer 元素即 300305；强制拆卡后完成态重建
仍带着整轮工具步骤，再撞 300305，整轮回落纯文本。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.feishu import FeishuClient
from hermes_fry_cards.streaming.segment_helper import estimate_tool_elements
from hermes_fry_cards.streaming.session import CardSession, SessionState


def _make_session(msg_id: str = "msg_budget") -> CardSession:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return CardSession(msg_id, "chat_budget", loop)


def _setup_ctrl() -> StreamCardController:
    ctrl = StreamCardController()
    ctrl._cfg._raw = {"streaming": {"enabled": True}, "feishu": {"app_id": "app", "app_secret": "secret"}}
    ctrl._client = AsyncMock(spec=FeishuClient)
    ctrl._client.cardkit_create = AsyncMock(return_value="card_new")
    ctrl._client.reply_card_by_id = AsyncMock(return_value="msg_new")
    ctrl._client.cardkit_batch_update = AsyncMock()
    ctrl._client.cardkit_stream_element = AsyncMock()
    ctrl._client.cardkit_close_streaming = AsyncMock()
    ctrl._client.cardkit_update = AsyncMock()
    return ctrl


@pytest.mark.asyncio
async def test_tool_panel_growth_counts_toward_element_count() -> None:
    """工具步骤增加时 element_count 必须随之增长，否则阈值拆卡永远不触发。"""
    ctrl = _setup_ctrl()
    session = _make_session()
    session.state = SessionState.STREAMING
    session.card_id = "card_budget"
    session.card_msg_id = "msg_budget_card"
    session.element_count = 1
    session.tool_use.record_start("read", "f0")
    session.segment_state.on_tool_event(1)
    ctrl._sessions[session.message_id] = session

    await ctrl._do_flush(session)
    first = session.element_count
    assert first >= estimate_tool_elements(0, 1, session.tool_use.build_display_steps())

    session.tool_use.record_start("read", "f1")
    session.segment_state.on_tool_event(len(session.tool_use.build_display_steps()))
    await ctrl._do_flush(session)

    steps = session.tool_use.build_display_steps()
    assert session.element_count >= estimate_tool_elements(0, 2, steps)
    assert session.element_count > first


@pytest.mark.asyncio
async def test_panel_update_after_split_carries_only_current_card_steps() -> None:
    """拆卡后新卡的面板只渲染本卡区间内的步骤，不能把旧卡步骤重发一遍。"""
    ctrl = _setup_ctrl()
    session = _make_session()
    session.state = SessionState.STREAMING
    session.card_id = "card_second"
    session.card_msg_id = "msg_second"
    session.split_index = 1
    session.element_count = 1
    session.tool_panel_created = True

    for idx in range(4):
        session.tool_use.record_start("read", f"f{idx}")
    session.segment_state.on_answer_delta("a")
    session.segment_state.on_tool_event(2)  # steps 0,1 已在旧卡
    session.segment_state.split_tool_segment(1, 2)  # 新卡从 step 2 起
    session.split_index = 2
    for seg in session.segment_state.segments:
        seg.created = True
        seg.dirty = True
    ctrl._sessions[session.message_id] = session

    await ctrl._do_flush(session)

    calls = ctrl._client.cardkit_batch_update.await_args_list
    assert calls
    payload = json.dumps(calls[-1].args[1], ensure_ascii=False)
    assert "f2" in payload and "f3" in payload
    assert "f0" not in payload and "f1" not in payload


@pytest.mark.asyncio
async def test_force_split_advances_split_index_so_completion_drops_sealed_content() -> None:
    """300305 强制拆卡后，split_index 必须前移，否则完成态重建会把旧卡内容再渲染一遍。"""
    ctrl = _setup_ctrl()
    session = _make_session()
    session.state = SessionState.STREAMING
    session.card_id = "card_old"
    session.card_msg_id = "msg_old"
    session.element_count = 190

    session.segment_state.on_answer_delta("first answer")
    session.segment_state.segments[0].created = True
    session.tool_use.record_start("read", "f0")
    session.segment_state.on_tool_event(1)
    session.segment_state.segments[1].created = True
    session.segment_state.on_answer_delta("second answer")
    ctrl._sessions[session.message_id] = session

    await ctrl._force_split_on_element_limit(session)

    assert session.split_index == 2
    assert [s.created for s in session.segment_state.segments] == [True, True, False]
