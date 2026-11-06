"""Interrupted terminal results use the card's existing stopped presentation."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.patch import on_message_completed_wait
from hermes_fry_cards.patcher import _complete_hook
from hermes_fry_cards.streaming.session import CardSession, SessionState


def flatten(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from flatten(child)
    elif isinstance(value, list):
        for child in value:
            yield from flatten(child)
    elif isinstance(value, str):
        yield value


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [True, False])
async def test_core_completion_hook_passes_interrupted_flag(interrupted):
    # Exercise the generated production hook rather than restating its arguments.
    captured = AsyncMock(return_value=False)
    source = "async def run_hook(response, self=None):\n" + _complete_hook("    ")
    namespace = {
        "agent_result": {"interrupted": interrupted},
        "event": type("Event", (), {"message_id": "current"})(),
        "response": "terminal response",
        "session_key": "fixture-session",
    }
    exec(source, namespace)
    with patch("hermes_fry_cards.patch.on_message_completed_wait", captured), \
            patch("hermes_fry_cards.patch.collect_stream_stats", return_value={}):
        await namespace["run_hook"]("terminal response")
    assert captured.await_count == 1
    assert captured.call_args.kwargs["is_interrupted"] is interrupted


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted,final_body,is_error", [
    (True, "Stopped before this turn started. Your message was not processed.", False),
    (True, "", False), (False, "completed answer", False),
    (True, "failed and interrupted", True),
])
async def test_stored_interrupt_result_paints_stopped_card(tmp_path, interrupted, final_body, is_error):
    import asyncio

    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {
        "streaming": {"enabled": True},
        "display": {"header_enabled": True},
        "feishu": {"app_id": "fixture"},
    }
    ctrl._client = AsyncMock()
    session = CardSession("current", "fixture-chat", asyncio.get_running_loop())
    session.set_card(card_id="fixture-card", card_msg_id="fixture-msg")
    session.state = SessionState.STREAMING
    ctrl._register_session(session, session_key="fixture-session")
    answer = final_body
    if not final_body:
        assert session.segment_state is not None
        session.segment_state.on_answer_delta("retained partial answer")
    with patch("hermes_fry_cards.patch.get_controller", return_value=ctrl), \
            patch.object(ctrl, "_maybe_send_completion_notice") as completion_notice:
        delivered = await on_message_completed_wait(
            message_id="current", answer=answer, is_error=is_error,
            is_interrupted=interrupted, session_key="fixture-session", duration=2,
        )
    assert delivered
    calls = ctrl._client.cardkit_update.await_args_list
    assert calls, "completion did not rebuild the card"
    card = calls[-1].args[1]
    texts = list(flatten(card))
    assert (answer or "retained partial answer") in "\n".join(texts)
    header_texts = list(flatten(card["header"]))
    assert any("Stopped" in s or "已停止" in s or "已中断" in s for s in header_texts) is (interrupted and not is_error)
    assert set(card["header"]) == {"title"}
    assert (session.state == SessionState.ABORTED) is (interrupted and not is_error)
    assert completion_notice.call_count == (1 if is_error or not interrupted else 0)
