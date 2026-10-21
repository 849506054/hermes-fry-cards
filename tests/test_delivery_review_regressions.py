"""Regression samples from the integrated delivery review."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from hermes_fry_cards import patch as hooks
from hermes_fry_cards import patcher
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.feishu import FeishuClient
from hermes_fry_cards.streaming.session import SessionState


@pytest.fixture
def ctrl(tmp_path):
    c = StreamCardController(tmp_path)
    c._cfg._raw = {"streaming": {"enabled": True}, "feishu": {"app_id": "app", "app_secret": "test"}}
    c._initialized = True
    c._swept_stale_cards = True
    c._client = AsyncMock(spec=FeishuClient)
    return c


def start(c, raw="A", anchor=None):
    with patch.object(c, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
        key = c.on_message_started(message_id=raw, anchor_id=anchor, chat_id="chat", session_key="key")
    return c._sessions[key] if key else None


async def complete_hook(c, owner):
    scope = {}
    exec(
        "async def run(event,agent_result,response,session_key):\n"
        + patcher._complete_hook("    ")
        + "    return agent_result\n",
        scope,
    )
    result = {"already_sent": True, "_hermes_lark_completion_id": owner}
    with patch.object(hooks, "get_controller", return_value=c):
        return await scope["run"](SimpleNamespace(message_id=owner), result, "final", "key")


@pytest.mark.asyncio
async def test_failed_completion_retains_registry_and_releases_plain_final(ctrl):
    s = start(ctrl)
    s.state = SessionState.STREAMING
    s.set_card(card_id="card", card_msg_id="card-message")
    ctrl._track_live_card(s)
    for name in ("cardkit_close_streaming", "cardkit_update", "cardkit_batch_update"):
        getattr(ctrl._client, name).side_effect = RuntimeError("offline")
    with patch("hermes_fry_cards.streaming.controller.asyncio.sleep", new_callable=AsyncMock):
        result = await complete_hook(ctrl, "A")
    assert not result.get("already_sent")
    assert "card-message" in ctrl._read_live_cards()
    fresh = StreamCardController(ctrl._profile_home)
    fresh._initialized = True
    fresh._client = AsyncMock(spec=FeishuClient)
    await fresh._sweep_stale_cards()
    fresh._client.patch_card_message.assert_awaited_once()
    assert fresh._read_live_cards() == {}


@pytest.mark.asyncio
async def test_shared_anchor_failed_raw_alias_releases_plain_final(ctrl):
    s = start(ctrl, anchor="thread")
    assert start(ctrl, "A2", "thread") is s
    s.mark_failed("offline")
    result = await complete_hook(ctrl, "A2")
    assert not result.get("already_sent")
    assert ctrl._text_fallback_needed == set()


@pytest.mark.asyncio
async def test_precreated_raw_gains_actual_reply_anchor(ctrl):
    s = start(ctrl, "B")
    assert start(ctrl, "B", "X") is s
    assert ctrl.resolve_session_id("X", "key") == "B"
    assert s.anchor_id == "X"
    assert ctrl.on_answer(message_id="X", text="delta", session_key="key")
    with patch.object(ctrl, "_complete_session_wait", new_callable=AsyncMock, return_value=True):
        s.set_card(card_id="card", card_msg_id="card-message")
        assert await ctrl.on_completed_wait(message_id="X", answer="final")


@pytest.mark.asyncio
async def test_disposed_closed_anchor_gives_new_raw_a_closed_alias(ctrl):
    s = start(ctrl, anchor="thread")
    s.state = SessionState.COMPLETED
    ctrl._dispose_session(s)
    assert start(ctrl, "late-raw", "thread") is None
    assert ctrl.on_answer(message_id="late-raw", text="late", session_key="key") is True
    assert await ctrl.on_completed_wait(message_id="late-raw", answer="late") is True


@pytest.mark.asyncio
async def test_initial_recovery_retries_after_transient_failure(ctrl):
    ctrl._swept_stale_cards = False
    ctrl._write_live_cards({"left": {"card_id": "old", "chat_id": "chat"}})
    ctrl._client.patch_card_message.side_effect = [RuntimeError("transient"), None]
    await ctrl._ensure_init()
    assert "left" in ctrl._read_live_cards()
    await ctrl._ensure_init()
    assert ctrl._client.patch_card_message.await_count == 2
    assert ctrl._read_live_cards() == {}
    assert ctrl._swept_stale_cards
