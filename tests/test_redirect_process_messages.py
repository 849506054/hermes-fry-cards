
import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from hermes_fry_cards.cardkit.builder import PROCESS_PANEL_ELEMENT_ID, body_content_elements
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.streaming.segments import SegmentState
from hermes_fry_cards.streaming.session import CardSession, SessionState


def make_controller():
    ctrl = cast(Any, StreamCardController())
    ctrl._cfg._raw = {"streaming": {"enabled": True}, "display": {
        "platforms": {"feishu": {"show_reasoning": True, "show_tool_use": True}},
    }}
    ctrl._client = AsyncMock()
    ctrl._schedule_flush = lambda _session: None
    return ctrl


def make_session(message_id):
    session = CardSession(message_id, "fixture-chat", asyncio.get_running_loop())
    session.set_card(card_id="card-" + message_id, card_msg_id="msg-" + message_id)
    session.state = SessionState.STREAMING
    return cast(Any, session)


def test_redirect_notice_stays_out_of_process_messages():
    state = SegmentState(max_reasoning_panels=None)
    state.on_interim_answer("进度一")
    state.add_notice("↪ 已重定向当前运行。", kind="redirect")
    state.on_completion_answer("定稿")
    assert state.completed_process_texts() == ["进度一"]


def test_redirect_carries_interim_messages_onto_new_card():
    old = SegmentState(max_reasoning_panels=None)
    old.on_interim_answer("旧卡进度一")
    old.on_interim_answer("旧卡进度二")
    new = SegmentState(max_reasoning_panels=None)
    new.adopt_process_messages(old)
    new.add_notice("↪ 已重定向当前运行。", kind="redirect")
    new.on_completion_answer("定稿")
    assert new.completed_process_texts() == ["旧卡进度一", "旧卡进度二"]


@pytest.mark.asyncio
async def test_redirected_card_keeps_earlier_progress_in_details():
    ctrl = make_controller()
    key = "feishu:dm:fixture"
    old = make_session("old-card")
    old.session_key = key
    ctrl._sessions[old.message_id] = old
    ctrl._session_keys[key] = old
    old.segment_state.on_interim_answer("重定向前进度一")
    old.segment_state.on_interim_answer("重定向前进度二")
    new = make_session("new-card")
    new.segment_state.add_notice("↪ 已重定向当前运行。", kind="redirect")
    ctrl._register_session(new, session_key=key)
    new.segment_state.on_completion_answer("定稿正文")
    assert await ctrl._do_complete_card_inner(new)
    card = ctrl._client.cardkit_update.call_args.args[1]
    panel = next(e for e in body_content_elements(card)
                 if e.get("element_id") == PROCESS_PANEL_ELEMENT_ID)
    assert [e["content"] for e in panel["elements"]] == ["重定向前进度一", "重定向前进度二"]
