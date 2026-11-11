"""Completed details retain visible progress while the body takes the final answer."""
import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from hermes_fry_cards.cardkit.builder import PROCESS_PANEL_ELEMENT_ID, body_content_elements
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.streaming.session import CardSession, SessionState

PROGRESS = "边界找到了：盒子里的代理栈都在跑。先把地址补回去。"  # noqa: RUF001


def make_session(reasoning=True, tools=False):
    ctrl = StreamCardController()
    ctrl._cfg._raw = {"streaming": {"enabled": True}, "display": {
        "platforms": {"feishu": {"show_reasoning": reasoning, "show_tool_use": tools}},
    }}
    ctrl._client = AsyncMock()
    session = CardSession("details-fixture", "fixture-chat", asyncio.get_running_loop())
    session.set_card(card_id="fixture-card", card_msg_id="fixture-msg")
    session.state = SessionState.STREAMING
    ctrl._sessions[session.message_id] = session
    ctrl._schedule_flush = lambda _session: None
    return cast(Any, ctrl), cast(Any, session)


def detail_texts(card):
    panel = next(e for e in body_content_elements(card)
                 if e.get("element_id") == PROCESS_PANEL_ELEMENT_ID)
    assert panel["expanded"] is False
    assert panel["header"]["position"] == "top"
    assert all(child["tag"] == "markdown" for child in panel["elements"])
    return [child["content"] for child in panel["elements"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("reasoning,tools", [(True, False), (False, True), (True, True), (False, False)])
async def test_progress_moves_into_details_in_each_display_mode(reasoning, tools):
    ctrl, session = make_session(reasoning, tools)
    ctrl.on_thinking(message_id=session.message_id, text=PROGRESS)
    ctrl.on_tool_update(message_id=session.message_id, tool_name="terminal", status="started")
    ctrl.on_tool_update(message_id=session.message_id, tool_name="terminal", status="completed")
    ctrl.on_thinking(message_id=session.message_id, text="地址已补回。正在检查重连。")
    ctrl.on_tool_update(message_id=session.message_id, tool_name="terminal", status="started")
    ctrl.on_thinking(message_id=session.message_id, text="已恢复连接。")
    ctrl._apply_completion_payload(session=session, answer="已恢复连接。", duration=10,
                                   model="", tokens=None, context=None)
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == [PROGRESS, "地址已补回。正在检查重连。"]
    assert [e["content"] for e in body_content_elements(card) if e["tag"] == "markdown"] == ["已恢复连接。"]
    assert "tool_totals" not in str(card)
    assert "Thought for" not in str(card)


@pytest.mark.asyncio
async def test_reasoning_stays_out_of_completed_details():
    ctrl, session = make_session()
    ctrl.on_reasoning(message_id=session.message_id, text=PROGRESS)
    ctrl.on_thinking(message_id=session.message_id, text="看到进度。")
    ctrl.on_tool_update(message_id=session.message_id, tool_name="terminal", status="started")
    ctrl.on_reasoning(message_id=session.message_id, text="隧道正在重连。")
    ctrl._apply_completion_payload(session=session, answer="定稿", duration=10,
                                   model="", tokens=None, context=None)
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    # 思考正文仍按原样显示在卡上；详情框只收更新期间的中途消息。
    assert detail_texts(card) == ["看到进度。"]
    assert PROGRESS not in detail_texts(card)


@pytest.mark.asyncio
async def test_repeated_completion_preserves_messages_once_and_rejects_late_delta():
    ctrl, session = make_session()
    state = session.segment_state
    state.on_interim_answer(PROGRESS)
    state.on_tool_event(1)
    state.on_answer_delta("最终正文")
    state.on_completion_answer("最终正文")
    state.on_answer_delta("迟到内容")
    state.on_completion_answer("重写定稿", replaced=True)
    state.on_completion_answer("重写定稿", replaced=True)
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == [PROGRESS]
    assert "最终正文" not in str(card) and "迟到内容" not in str(card)


@pytest.mark.asyncio
async def test_notice_slot_history_survives_cover_and_final_seal():
    ctrl, session = make_session()
    state = session.segment_state
    state.add_notice("回执一", "background")
    state.add_notice("回执二", "background")
    state.add_notice("回执二", "background")
    assert state.notice_slot_target() == "回执二"
    state.bind_notice_element("fry_notice", "回执二")
    state.on_interim_answer(PROGRESS)
    state.on_tool_event(1)
    state.on_completion_answer("定稿")
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == [PROGRESS]
    assert state.notices == []


@pytest.mark.asyncio
async def test_streamed_answer_deltas_stay_out_of_details():
    ctrl, session = make_session()
    ctrl.on_answer(message_id=session.message_id, text="最终正文第一段")
    ctrl.on_tool_update(message_id=session.message_id, tool_name="terminal", status="started")
    ctrl.on_answer(message_id=session.message_id, text="最终正文第二段")
    ctrl._apply_completion_payload(session=session, answer="最终正文第一段最终正文第二段",
                                   duration=5, model="", tokens=None, context=None)
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == ["No interim messages in this turn."]


@pytest.mark.asyncio
async def test_panel_keeps_placeholder_when_turn_has_no_interim_messages():
    ctrl, session = make_session()
    ctrl.on_tool_update(message_id=session.message_id, tool_name="terminal", status="started")
    ctrl.on_tool_update(message_id=session.message_id, tool_name="terminal", status="completed")
    ctrl._apply_completion_payload(session=session, answer="定稿正文", duration=5,
                                   model="", tokens=None, context=None)
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == ["No interim messages in this turn."]
    assert "定稿正文" in str(card)


@pytest.mark.asyncio
async def test_panel_appears_when_tools_are_hidden_but_present():
    ctrl, session = make_session(reasoning=False, tools=False)
    ctrl.on_tool_update(message_id=session.message_id, tool_name="terminal", status="started")
    ctrl.on_tool_update(message_id=session.message_id, tool_name="terminal", status="completed")
    ctrl._apply_completion_payload(session=session, answer="定稿正文", duration=5,
                                   model="", tokens=None, context=None)
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == ["No interim messages in this turn."]


@pytest.mark.asyncio
async def test_final_answer_never_appears_in_details():
    ctrl, session = make_session()
    state = session.segment_state
    state.on_interim_answer("进度一")
    state.on_tool_event(1)
    state.on_answer_delta("第一段定稿。")
    state.on_tool_event(2)
    state.on_answer_delta("第二段定稿。")
    state.on_completion_answer("第一段定稿。第二段定稿。")
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == ["进度一"]
    assert "定稿" not in "".join(detail_texts(card))


@pytest.mark.asyncio
async def test_preview_delta_than_final_with_spacing_is_still_dropped():
    ctrl, session = make_session()
    state = session.segment_state
    state.on_interim_answer("进度一")
    state.on_tool_event(1)
    state.on_answer_delta("定 稿")
    state.on_completion_answer("定 稿")
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == ["进度一"]


@pytest.mark.asyncio
async def test_preview_equal_to_final_is_kept_only_as_body():
    ctrl, session = make_session()
    session.segment_state.on_answer_delta("定稿的流式预览")
    session.segment_state.on_completion_answer("定稿的流式预览")
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == ["No interim messages in this turn."]


@pytest.mark.asyncio
async def test_consecutive_interim_messages_with_unstreamed_final_keep_boundaries():
    ctrl, session = make_session()
    ctrl.on_thinking(message_id=session.message_id, text="第一条过程消息")
    ctrl.on_thinking(message_id=session.message_id, text="第二条过程消息")
    session.segment_state.on_completion_answer("只通过完成事件到达的定稿")
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == ["第一条过程消息", "第二条过程消息"]


@pytest.mark.asyncio
async def test_rollover_retains_each_cards_own_history():
    ctrl, session = make_session()
    state = session.segment_state
    state.on_interim_answer("旧卡进度一")
    state.on_interim_answer("旧卡进度二")
    state.on_tool_event(1)
    seal_segments = list(state.segments)
    state.on_interim_answer("新卡进度")
    state.on_tool_event(2)
    await ctrl._seal_current_card(session, seal_segments)
    old_card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(old_card) == ["旧卡进度一", "旧卡进度二"]
    session.split_index = len(seal_segments)
    state.on_completion_answer("最终定稿")
    assert await ctrl._do_complete_card_inner(session)
    new_card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(new_card) == ["新卡进度"]


@pytest.mark.asyncio
async def test_answer_limit_still_preserves_message_boundaries():
    ctrl, session = make_session()
    state = session.segment_state
    state.max_answer_segments = 1
    for i in range(12):
        state.on_interim_answer(f"进度{i}")
        state.on_tool_event(i + 1)
    state.on_answer_delta("定稿")
    state.on_completion_answer("定稿")
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert detail_texts(card) == [f"进度{i}" for i in range(12)]


@pytest.mark.asyncio
async def test_stopped_card_has_no_details_panel():
    """停止卡不建框：停止后由新卡接手，旧卡不留计数与过程框。"""
    ctrl, session = make_session()
    state = session.segment_state
    state.on_interim_answer(PROGRESS)
    state.on_tool_event(1)
    state.on_answer_delta("被中断前的正文")
    session.state = SessionState.ABORTED
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    body = body_content_elements(card)
    assert PROCESS_PANEL_ELEMENT_ID not in [e.get("element_id") for e in body]
    assert not any(e.get("tag") == "collapsible_panel" for e in body)
    assert "被中断前的正文" in str(card)


def test_stopped_card_builder_skips_panel_but_completed_keeps_it():
    from hermes_fry_cards.cardkit.builder import build_complete_card

    stopped = build_complete_card(segments=[], all_tool_steps=[], is_aborted=True,
                                  footer_data={"duration": 5}, process_messages=[PROGRESS])
    assert PROCESS_PANEL_ELEMENT_ID not in [e.get("element_id") for e in body_content_elements(stopped)]
    completed = build_complete_card(segments=[], all_tool_steps=[],
                                    footer_data={"duration": 5}, process_messages=[PROGRESS])
    assert PROCESS_PANEL_ELEMENT_ID in [e.get("element_id") for e in body_content_elements(completed)]
