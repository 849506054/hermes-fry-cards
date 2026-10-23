"""活跃投递登记与重启恢复：原子读改写和唯一收口。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.feishu import FeishuClient
from hermes_fry_cards.streaming.session import CardSession, SessionState


@pytest.fixture
def ctrl(tmp_path):
    c = StreamCardController(tmp_path)
    c._cfg._raw = {
        "streaming": {"enabled": True},
        "feishu": {"app_id": "app", "app_secret": "secret"},
    }
    c._initialized = True
    c._client = AsyncMock(spec=FeishuClient)
    c._client.cardkit_create.return_value = "next-card"
    c._client.reply_card_by_id.return_value = "next-message"
    return c


def session(ctrl):
    s = CardSession("source", "chat", asyncio.get_running_loop())
    s.state = SessionState.STREAMING
    s.set_card(card_id="card", card_msg_id="message")
    ctrl._register_session(s, session_key="key")
    return s


@pytest.mark.asyncio
async def test_sweep_closes_cards_left_live(ctrl):
    ctrl._write_live_cards({"left": {"card_id": "old", "chat_id": "chat"}})
    await ctrl._sweep_stale_cards()
    ctrl._client.patch_card_message.assert_awaited_once()
    assert ctrl._client.patch_card_message.await_args.args[0] == "left"
    assert not ctrl._live_cards_path().exists()


@pytest.mark.asyncio
async def test_sweep_keeps_failed_recovery_and_current_process_entries(ctrl):
    current = session(ctrl)
    ctrl._track_live_card(current)
    data = ctrl._read_live_cards()
    data["left"] = {"card_id": "old", "chat_id": "chat"}
    ctrl._write_live_cards(data)
    ctrl._client.patch_card_message.side_effect = RuntimeError("offline")
    await ctrl._sweep_stale_cards()
    assert set(ctrl._read_live_cards()) == {"left", "message"}
    assert ctrl._client.patch_card_message.await_args.args[0] == "left"


@pytest.mark.asyncio
async def test_unique_completion_preserves_recovery_registration_until_success(ctrl):
    for outcome in (True, False, RuntimeError("unexpected")):
        s = session(ctrl)
        ctrl._track_live_card(s)
        kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
        with patch.object(ctrl, "_do_complete_card_inner", new_callable=AsyncMock, **kwargs):
            if isinstance(outcome, Exception):
                with pytest.raises(RuntimeError):
                    await ctrl._do_complete_card(s)
            else:
                assert await ctrl._do_complete_card(s) is outcome
        if outcome is True:
            assert ctrl._read_live_cards() == {}
        else:
            assert "message" in ctrl._read_live_cards()
            await ctrl._sweep_stale_cards()
            assert ctrl._read_live_cards() == {}
    assert ctrl._client.patch_card_message.await_count == 2


@pytest.mark.asyncio
async def test_split_replaces_registration_with_current_physical_page(ctrl):
    s = session(ctrl)
    ctrl._track_live_card(s)
    s.segment_state.on_answer_delta("page one")
    s.segment_state.on_tool_event(1)
    s.segment_state.segments[0].created = True
    assert await ctrl._do_split_card(s, 1, [], set(), {}, [])
    assert set(ctrl._read_live_cards()) == {"next-message"}
    assert ctrl._read_live_cards()["next-message"]["card_id"] == "next-card"
    assert await ctrl._do_complete_card(s)
    assert ctrl._read_live_cards() == {}


@pytest.mark.asyncio
async def test_sweep_read_modify_preserves_new_registration(ctrl):
    ctrl._write_live_cards({"left": {"card_id": "old", "chat_id": "chat"}})
    s = session(ctrl)

    async def during_patch(message_id, card):
        ctrl._track_live_card(s)

    ctrl._client.patch_card_message.side_effect = during_patch
    await ctrl._sweep_stale_cards()
    assert set(ctrl._read_live_cards()) == {"message"}


@pytest.mark.asyncio
async def test_corrupt_registry_is_preserved(ctrl):
    path = ctrl._live_cards_path()
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    s = session(ctrl)
    ctrl._track_live_card(s)
    await ctrl._sweep_stale_cards()
    assert path.read_text(encoding="utf-8") == "{broken"
    ctrl._client.patch_card_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_registry_preserves_unrelated_record_fields(ctrl):
    ctrl._write_live_cards({"other": {"opaque": [1, 2], "note": "keep"}})
    s = session(ctrl)
    ctrl._track_live_card(s)
    ctrl._untrack_live_card(s)
    assert json.loads(ctrl._live_cards_path().read_text()) == {"other": {"opaque": [1, 2], "note": "keep"}}


@pytest.mark.asyncio
async def test_initial_recovery_gives_up_after_two_attempts(ctrl):
    """一条封不掉的登记不能在每轮对话里反复请求飞书。"""
    ctrl._swept_stale_cards = False
    ctrl._write_live_cards({"dead": {"card_id": "old", "chat_id": "chat"}})
    ctrl._client.patch_card_message.side_effect = RuntimeError("invalid id")
    await ctrl._ensure_init()
    await ctrl._ensure_init()
    await ctrl._ensure_init()
    assert ctrl._client.patch_card_message.await_count == 2
    assert ctrl._read_live_cards()["dead"]["attempts"] == 2


@pytest.mark.asyncio
async def test_sweep_drops_record_after_three_failed_processes(ctrl):
    ctrl._write_live_cards({"dead": {"card_id": "old", "chat_id": "chat", "attempts": 2}})
    ctrl._client.patch_card_message.side_effect = RuntimeError("invalid id")
    await ctrl._sweep_stale_cards()
    assert "dead" not in ctrl._read_live_cards()
