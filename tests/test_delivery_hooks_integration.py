"""Exercise the real injected busy hook against the real delivery controller."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from hermes_fry_cards import patch as hooks
from hermes_fry_cards import patcher as injector
from hermes_fry_cards.controller import StreamCardController


@pytest.mark.asyncio
async def test_injected_busy_notice_uses_current_card_not_new_inbound(tmp_path):
    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {"streaming": {"enabled": True}, "feishu": {"app_id": "app", "app_secret": "test-only"}}
    with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
        ctrl.on_message_started(message_id="current", chat_id="chat", session_key="key")
    session = ctrl._sessions["current"]
    core = SimpleNamespace(
        _reply_anchor_for_event=lambda event: event.message_id,
        _send_busy_ack_reply=AsyncMock(),
    )
    scope = {}
    body = injector._busy_ack_hook("    ")
    exec(
        "async def run(self,event,session_key,message,adapter):\n"
        + body
        + "    await self._send_busy_ack_reply(event,adapter,message)\n"
        + "    return True\n",
        scope,
    )
    with patch.object(hooks, "get_controller", return_value=ctrl), patch.object(ctrl, "_schedule_flush"):
        assert await scope["run"](core, SimpleNamespace(message_id="new-busy-input"), "key", "queued", None)
    core._send_busy_ack_reply.assert_not_awaited()
    assert [seg.text for seg in session.segment_state.segments] == ["queued"]
    assert not session.flush.completed


@pytest.mark.asyncio
async def test_injected_busy_notice_during_completion_returns_to_core(tmp_path):
    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {"streaming": {"enabled": True}, "feishu": {"app_id": "app", "app_secret": "test-only"}}
    with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
        ctrl.on_message_started(message_id="current", chat_id="chat", session_key="key")
    session = ctrl._sessions["current"]
    session.flush.mark_completed()
    with patch.object(hooks, "get_controller", return_value=ctrl):
        assert hooks.on_busy_ack(message_id="new-busy-input", session_key="key", text="queued") is False
    assert session.segment_state.segments == []


@pytest.mark.asyncio
async def test_completed_flush_waiter_drains_before_completion_api(tmp_path):
    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {"streaming": {"enabled": True}, "feishu": {"app_id": "app", "app_secret": "test-only"}}
    with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
        ctrl.on_message_started(message_id="current", chat_id="chat", session_key="key")
    session = ctrl._sessions["current"]
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def slow_flush():
        entered.set()
        await gate.wait()

    task = session.flush._create_flush_task(slow_flush)
    await entered.wait()
    session.flush.mark_completed()
    waiter = asyncio.create_task(session.flush.wait_for_flush())
    await asyncio.sleep(0)
    assert not waiter.done()
    gate.set()
    await asyncio.wait_for(asyncio.gather(task, waiter), 1)
