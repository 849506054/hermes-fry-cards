"""Redirect placeholder gives its element to the first answer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from hermes_fry_cards.cardkit.builder import LOADING_ELEMENT_ID, TOOL_PANEL_ELEMENT_ID, build_complete_card
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.feishu import FeishuClient
from hermes_fry_cards.streaming.segments import SegmentState, SegmentType

PROMPT = "↪ 已重定向当前运行 · 正在按你的修正调整…"


def test_first_answer_reuses_placeholder_element_after_other_events():
    state = SegmentState()
    prompt = state.add_notice(PROMPT, sticky=True)
    prompt.created = True
    state.on_reasoning_delta("thinking")
    state.on_tool_event(1)
    state.add_notice("background receipt")
    state.on_answer_delta("hello")
    assert prompt.type == SegmentType.ANSWER
    assert prompt.el_id == "sticky_notice"
    assert prompt.created
    assert prompt.dirty
    assert prompt.text == "hello"
    assert all(s.type != SegmentType.STICKY for s in state.segments)
    state.on_answer_delta(" world")
    assert prompt.text == "hello world"
    assert any(s.text == "background receipt" for s in state.segments)


def test_final_only_answer_replaces_placeholder():
    state = SegmentState()
    prompt = state.add_notice(PROMPT, sticky=True)
    state.on_completion_answer("final")
    assert prompt.type == SegmentType.ANSWER
    assert prompt.text == "final"
    assert [s.text for s in state.segments] == ["final"]


def test_empty_delta_preserves_initial_prompt():
    state = SegmentState()
    prompt = state.add_notice(PROMPT, sticky=True)
    state.on_answer_delta("")
    assert prompt.type == SegmentType.STICKY
    assert prompt.text == PROMPT


@pytest.mark.parametrize("aborted,error", [(False, False), (True, False), (False, True)])
def test_final_card_excludes_pending_placeholder(aborted, error):
    state = SegmentState()
    state.add_notice(PROMPT, sticky=True)
    state.add_notice("background receipt")
    card = build_complete_card(
        segments=state.segments, all_tool_steps=[], is_aborted=aborted, is_error=error,
    )
    body = json.dumps(card, ensure_ascii=False)
    assert PROMPT not in body
    assert "background receipt" in body


@pytest.mark.asyncio
async def test_real_redirect_create_stream_and_complete_payloads(tmp_path):
    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {"streaming": {"enabled": True}, "feishu": {"app_id": "app", "app_secret": "test"}}
    ctrl._initialized = ctrl._swept_stale_cards = True
    ctrl._client = AsyncMock(spec=FeishuClient)
    ctrl._client.cardkit_create.return_value = "card-B"
    ctrl._client.reply_card_by_id.return_value = "message-B"
    with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coroutine, loop: coroutine.close()):
        ctrl.on_message_started(message_id="A", chat_id="chat", session_key="key")
        ctrl.on_turn_redirected(message_id="B", session_key="key")
    session = ctrl._sessions["B"]
    await ctrl._do_create_card(session)
    initial = ctrl._client.cardkit_create.await_args.args[0]
    ids = [el.get("element_id") for el in initial["body"]["elements"]]
    assert ids.index("sticky_notice") == ids.index(TOOL_PANEL_ELEMENT_ID) + 1
    assert ids.index(LOADING_ELEMENT_ID) == ids.index("sticky_notice") + 1
    with patch.object(ctrl, "_schedule_flush"):
        assert ctrl.on_answer(message_id="B", session_key="key", text="hello")
    await ctrl._do_flush(session)
    updates = ctrl._client.cardkit_stream_element.await_args_list
    assert any(c.args[1:3] == ("sticky_notice", "hello") for c in updates)
    assert await ctrl.on_completed_wait(message_id="B", answer="hello")
    final = ctrl._client.cardkit_update.await_args.args[1]
    assert PROMPT not in json.dumps(final, ensure_ascii=False)
    assert "hello" in json.dumps(final, ensure_ascii=False)
