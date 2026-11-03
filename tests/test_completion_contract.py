"""Completed cards render the authoritative answer independently of preview chunks."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.streaming.segments import SegmentState, SegmentType
from hermes_fry_cards.streaming.session import CardSession, SessionState


def answers(state):
    return [s.text for s in state.segments if s.type == SegmentType.ANSWER and s.text]


@pytest.mark.parametrize("preview", [
    ["旁白", "预览"], ["\n\n甲", "乙"], ["完整答案", "完整答案"],
    ["旧稿", "重写稿"], ["表头", "表尾"], ["对照之下，", "对照之下，正文"],  # noqa: RUF001
])
def test_final_is_only_body_for_any_preview(preview):
    state = SegmentState()
    state.on_reasoning_delta("起始思考")
    for text in preview:
        state.on_answer_delta(text)
        state.on_reasoning_delta("思考")
    state.on_completion_answer("完整答案")
    assert answers(state) == ["完整答案"]
    assert len([s for s in state.segments if s.type == SegmentType.REASONING]) == 3


def test_empty_final_clears_preview_and_preserves_process():
    state = SegmentState()
    state.on_reasoning_delta("思考")
    state.on_answer_delta("中途旁白")
    state.on_completion_answer("")
    assert answers(state) == []
    assert state.segments[0].text == "思考"


def test_completion_is_idempotent_and_late_delta_stays_out():
    state = SegmentState()
    state.on_answer_delta("旧稿")
    state.on_completion_answer("定稿")
    state.on_answer_delta("迟到增量")
    assert answers(state) == ["定稿"]
    state.on_completion_answer("第二版定稿", replaced=True)
    state.on_answer_delta("旧版尾巴")
    state.on_completion_answer("第二版定稿", replaced=True)
    assert answers(state) == ["第二版定稿"]


def test_final_internal_repetition_is_preserved():
    state = SegmentState()
    state.on_answer_delta("预览")
    final = "一句话。\n一句话。"
    state.on_completion_answer(final)
    assert answers(state) == [final]


def test_all_chunk_sizes_produce_same_final():
    final = "|字段|值|\n|---|---|\n|甲|乙|\n\n中文正文。" * 4
    for size in range(1, len(final) + 1):
        state = SegmentState()
        state.on_answer_delta("我先查一下。")
        for index in range(0, len(final), size):
            state.on_reasoning_delta("思考")
            state.on_answer_delta("\n\n" + final[index:index + size])
        state.on_completion_answer(final)
        assert answers(state) == [final]


def make_session():
    ctrl = StreamCardController()
    ctrl._cfg._raw = {"streaming": {"enabled": True}, "feishu": {"app_id": "fixture"}}
    ctrl._client = AsyncMock()
    session = CardSession("final_fixture", "fixture_chat", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.card_id = "fixture_card"
    session.card_msg_id = "fixture_msg"
    return ctrl, session


def body_texts(card):
    return [e.get("content", "") for e in card["body"]["elements"] if e["tag"] == "markdown"]


@pytest.mark.asyncio
@pytest.mark.parametrize("active_preview", [True, False])
async def test_final_renders_on_active_card_after_split(active_preview):
    ctrl, session = make_session()
    state = session.segment_state
    state.on_answer_delta("前卡预览")
    state.on_reasoning_delta("本卡思考")
    session.split_index = 1
    if active_preview:
        state.on_answer_delta("当前预览")
    ctrl._apply_completion_payload(session=session, answer="唯一完整定稿", duration=1,
                                   model="", tokens=None, context=None)
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert body_texts(card) == ["唯一完整定稿"]


@pytest.mark.asyncio
async def test_rollover_card_keeps_process_and_continuation_status():
    ctrl, session = make_session()
    session.segment_state.on_answer_delta("应当消失的预览")
    session.segment_state.on_reasoning_delta("思考内容" * 50)
    session.footer["duration"] = 60
    await ctrl._seal_current_card(session, session.active_segments())
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert "应当消失的预览" not in str(card)
    assert any("continues" in t for t in body_texts(card))
    assert any(e["tag"] == "collapsible_panel" for e in card["body"]["elements"])


@pytest.mark.asyncio
@pytest.mark.parametrize("final", ["", "<think>仅思考</think>"])
async def test_empty_success_final_clears_interlude(final):
    ctrl, session = make_session()
    session.segment_state.on_answer_delta("中途旁白")
    ctrl._apply_completion_payload(session=session, answer=final, duration=1,
                                   model="", tokens=None, context=None)
    assert answers(session.segment_state) == []


@pytest.mark.asyncio
async def test_abort_without_final_keeps_partial_body():
    ctrl, session = make_session()
    session.segment_state.on_answer_delta("已经写出的半段")
    session.state = SessionState.ABORTED
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert "已经写出的半段" in body_texts(card)


@pytest.mark.asyncio
async def test_completion_waits_for_rollover_flush():
    ctrl, session = make_session()
    session.segment_state.on_answer_delta("换卡前预览")
    ctrl._sessions[session.message_id] = session
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending_rollover():
        entered.set()
        await release.wait()
        session.split_index = len(session.segment_state.segments)

    session.flush.set_card_message_ready(True)
    flushing = asyncio.create_task(session.flush.flush_now(pending_rollover))
    await entered.wait()
    ctrl._complete_session_wait = AsyncMock(return_value=True)
    completing = asyncio.create_task(ctrl.on_completed_wait(
        message_id=session.message_id, answer="换卡后定稿",
    ))
    try:
        await asyncio.sleep(0)
        assert not completing.done()
        release.set()
        await flushing
        assert await completing
        assert [s.text for s in session.active_segments() if s.type == SegmentType.ANSWER] == ["换卡后定稿"]
    finally:
        release.set()
        await asyncio.gather(flushing, completing)


@pytest.mark.asyncio
async def test_completion_drains_queued_flush_before_it_starts():
    ctrl, session = make_session()
    session.segment_state.on_answer_delta("排队预览")
    ctrl._sessions[session.message_id] = session
    ctrl._complete_session_wait = AsyncMock(return_value=True)
    session.flush.set_card_message_ready(True)
    queued_write = AsyncMock()
    session.flush._do_flush_task(queued_write)
    session.flush.mark_completed()
    result = await asyncio.wait_for(ctrl.on_completed_wait(
        message_id=session.message_id, answer="唯一完整定稿",
    ), timeout=0.5)
    assert result
    queued_write.assert_not_awaited()
    assert answers(session.segment_state) == ["唯一完整定稿"]


@pytest.mark.asyncio
async def test_error_completion_without_final_preserves_partial():
    ctrl, session = make_session()
    session.segment_state.on_answer_delta("部分回答")
    ctrl._sessions[session.message_id] = session
    ctrl._complete_session_wait = AsyncMock(return_value=True)
    assert await ctrl.on_completed_wait(message_id=session.message_id, is_error=True)
    assert answers(session.segment_state) == ["部分回答"]
