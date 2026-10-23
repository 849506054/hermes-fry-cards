"""Execute busy-receipt, redirect and watcher injection branches."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_fry_cards import patch as hooks
from hermes_fry_cards import patcher as injector


def _run_hook(body: str, params: str, suffix: str = ""):
    scope = {}
    exec(f"async def run({params}):\n{body}{suffix}", scope)
    return scope["run"]


@pytest.mark.parametrize("claimed", [True, False])
def test_busy_receipt_claim_falls_through_only_when_needed(claimed: bool) -> None:
    runner = MagicMock()
    runner._reply_anchor_for_event.return_value = "real-anchor"
    runner._send_busy_ack_reply = AsyncMock()
    event = SimpleNamespace(message_id="raw-inbound")
    body = injector._busy_ack_hook("    ")
    suffix = "    await self._send_busy_ack_reply(event, adapter, message)\n    return True\n"
    with patch.object(hooks, "on_busy_ack", return_value=claimed) as ack:
        result = asyncio.run(_run_hook(body, "self, event, session_key, message, adapter", suffix)(
            runner, event, "sk", "busy", MagicMock(),
        ))
    assert result is True
    ack.assert_called_once_with(message_id="real-anchor", session_key="sk", text="busy")
    assert runner._send_busy_ack_reply.await_count == (0 if claimed else 1)


def test_busy_redirect_gate_uses_upstream_configuration() -> None:
    runner = MagicMock()
    runner._busy_steer_ack_enabled.return_value = False
    body = injector._busy_ack_gate_hook("    ")
    run = _run_hook(body, "self, event, session_key, is_redirect_mode", "    return False\n")
    assert asyncio.run(run(runner, MagicMock(), "sk", True)) is True
    assert asyncio.run(run(runner, MagicMock(), "sk", False)) is False


def test_busy_redirect_gate_falls_through_when_ack_enabled() -> None:
    runner = MagicMock()
    runner._busy_steer_ack_enabled.return_value = True
    body = injector._busy_ack_gate_hook("    ")
    run = _run_hook(body, "self, event, session_key, is_redirect_mode", "    return False\n")
    assert asyncio.run(run(runner, MagicMock(), "sk", True)) is False


def test_redirect_signal_uses_real_anchor() -> None:
    with patch.object(hooks, "on_turn_redirected") as redirected:
        asyncio.run(_run_hook(injector._turn_redirected_hook("    "), "session_key, anchor")("sk", "real-anchor"))
    redirected.assert_called_once_with(session_key="sk", message_id="real-anchor")


@pytest.mark.parametrize("claimed,finished", [(True, True), (False, True), (True, False)])
def test_watcher_finished_notice_card_claim_and_plain_fallback(claimed: bool, finished: bool) -> None:
    scope = {"t": lambda key: "Finished" if key.endswith("task_finished") else "Failed"}
    exec("async def run(platform_name, chat_id, message_text, watcher):\n" + injector._watcher_notice_hook("    ")
         + "    return 'plain'\n", scope)
    text = "Finished task" if finished else "Still working"
    with patch.object(hooks, "on_background_deliver", new=AsyncMock(return_value=claimed)) as deliver:
        result = asyncio.run(scope["run"]("feishu", "chat", text, {"message_id": "owner"}))
    assert result == (None if claimed and finished else "plain")
    assert deliver.await_count == (1 if finished else 0)
    if finished:
        assert deliver.call_args.kwargs["reply_to_message_id"] == "owner"


def test_stale_abort_uses_result_completion_owner() -> None:
    event = SimpleNamespace(message_id="old-raw")
    with patch.object(hooks, "on_message_aborted") as aborted:
        asyncio.run(_run_hook(injector._abort_hook("    "), "event, agent_result")(
            event, {"_hermes_lark_completion_id": "redirect-anchor"},
        ))
    aborted.assert_called_once_with(message_id="redirect-anchor")
