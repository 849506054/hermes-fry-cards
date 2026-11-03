"""Compression preserves the turn's card until final delivery is acknowledged."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.feishu import FeishuClient
from hermes_fry_cards.patcher import _complete_hook
from hermes_fry_cards.streaming.session import CardSession, SessionState


def make_turn():
    loop = asyncio.get_running_loop()
    ctrl = StreamCardController()
    ctrl._cfg._raw = {"streaming": {"enabled": True}, "feishu": {"app_id": "fixture"}}
    ctrl._initialized = True
    ctrl._loop = loop
    ctrl._client = AsyncMock(spec=FeishuClient)
    session = CardSession("msg_compression", "chat_fixture", loop)
    session.set_card(card_id="card_compression", card_msg_id="reply_compression")
    session.state = SessionState.STREAMING
    ctrl._register_session(session, session_key="turn_fixture")
    return ctrl, session


async def gateway_complete(ctrl, session, answer, *, already_sent=False):
    """Execute the shipped completion hook, including its gateway send decision."""
    scope = {
        "agent_result": {"already_sent": True} if already_sent else {},
        "event": SimpleNamespace(message_id=session.message_id),
        "response": answer, "session_key": session.session_key,
    }
    code = "async def complete(response=response):\n" + _complete_hook("    ") + "\n    return agent_result\n"
    exec(compile(code, "<gateway-completion-hook>", "exec"), scope)
    with patch("hermes_fry_cards.patch.get_controller", return_value=ctrl):
        return await scope["complete"]()


def body_answers(card):
    return [el["content"] for el in card["body"]["elements"] if el.get("tag") == "markdown"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["sync", "async"])
@pytest.mark.parametrize("state", [SessionState.IDLE, SessionState.CREATING, SessionState.STREAMING])
async def test_compression_preserves_live_turn(entry, state):
    ctrl, session = make_turn()
    session.state = state
    original = (session.card_id, session.card_msg_id, session.sequence, session.split_index)
    if entry == "sync":
        accepted = await asyncio.to_thread(ctrl.on_compression, session_key=session.session_key)
    else:
        accepted = await ctrl.on_session_compressed(session_key=session.session_key)
    await asyncio.sleep(0)
    assert accepted is True
    assert session.state == state
    assert not session.flush._completed
    assert ctrl._sessions[session.message_id] is session
    assert (session.card_id, session.card_msg_id, session.sequence, session.split_index) == original
    assert ctrl._client.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", ["", "short preview", "outdated draft with Final answer. inside"])
async def test_final_replaces_preview_on_same_card_after_compression(preview):
    ctrl, session = make_turn()
    assert session.segment_state is not None
    if preview:
        session.segment_state.on_answer_delta(preview)
    assert ctrl.on_compression(session_key=session.session_key)
    result = await gateway_complete(ctrl, session, "Final answer.")
    assert result.get("already_sent") is True
    ctrl._client.cardkit_update.assert_awaited_once()
    call = ctrl._client.cardkit_update.call_args
    assert call.args[0] == "card_compression"
    assert body_answers(call.args[1]) == ["Final answer."]
    ctrl._client.cardkit_close_streaming.assert_awaited_once()
    ctrl._client.cardkit_create.assert_not_called()
    assert session.message_id not in ctrl._sessions
    assert session.session_key not in ctrl._session_keys


@pytest.mark.asyncio
async def test_delivery_waits_for_cardkit_ack():
    ctrl, session = make_turn()
    assert session.segment_state is not None
    session.segment_state.on_answer_delta("Final answer.")
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_update(*args, **kwargs):
        started.set()
        await release.wait()

    ctrl._client.cardkit_update.side_effect = delayed_update
    assert ctrl.on_compression(session_key=session.session_key)
    task = asyncio.create_task(gateway_complete(ctrl, session, "Final answer."))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert not task.done()
        assert session.state != SessionState.COMPLETED
        assert ctrl._sessions[session.message_id] is session
    finally:
        release.set()
        result = await asyncio.wait_for(task, 2)
    assert result.get("already_sent") is True
    assert session.state == SessionState.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize("already_sent", [False, True])
async def test_cardkit_failure_preserves_gateway_fallback(already_sent):
    ctrl, session = make_turn()
    assert session.segment_state is not None
    session.segment_state.on_answer_delta("Final answer.")
    ctrl._client.cardkit_update.side_effect = RuntimeError("injected CardKit failure")
    assert ctrl.on_compression(session_key=session.session_key)
    # Use the real completion retries and the actual gateway hook's fallback branch.
    with patch("hermes_fry_cards.streaming.controller.asyncio.sleep", new=AsyncMock()):
        result = await gateway_complete(ctrl, session, "Final answer.", already_sent=already_sent)
    assert not result.get("already_sent")
    assert session.state == SessionState.FAILED
    assert ctrl._client.cardkit_update.await_count >= 3
    assert session.message_id not in ctrl._sessions


@pytest.mark.asyncio
async def test_repeated_compression_allows_following_deltas():
    ctrl, session = make_turn()
    assert session.segment_state is not None
    assert ctrl.on_compression(session_key=session.session_key)
    assert ctrl.on_compression(message_id=session.message_id, session_key=session.session_key)
    assert ctrl.on_answer(message_id=session.message_id, text="After compression.")
    await session.flush.wait_for_flush()
    result = await gateway_complete(ctrl, session, "Final answer.")
    assert result.get("already_sent") is True
    ctrl._client.cardkit_update.assert_awaited_once()
    assert body_answers(ctrl._client.cardkit_update.call_args.args[1]) == ["Final answer."]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [SessionState.COMPLETED, SessionState.ABORTED, SessionState.FAILED])
async def test_terminal_card_keeps_terminal_state(state):
    ctrl, session = make_turn()
    session.state = state
    assert ctrl.on_compression(session_key=session.session_key) is False
    assert session.state == state
    assert ctrl._client.mock_calls == []


@pytest.mark.asyncio
async def test_missing_turn_keeps_other_turn_untouched():
    ctrl, session = make_turn()
    before = session.last_activity
    assert ctrl.on_compression(message_id="msg_old", session_key=session.session_key) is False
    assert session.last_activity == before
    assert ctrl.on_compression(session_key="missing") is False
    assert ctrl._client.mock_calls == []
