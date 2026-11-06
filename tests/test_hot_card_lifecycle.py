"""Configuration edits affect new cards while admitted cards keep their ownership."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.patch import on_message_completed_wait
from hermes_fry_cards.streaming.flush import FlushController
from hermes_fry_cards.streaming.session import CardSession, SessionState


def write_config(home, **streaming):
    home.mkdir(exist_ok=True)
    (home / "config.yaml").write_text(yaml.safe_dump({
        "streaming": {"enabled": True, **streaming},
        "display": {"show_reasoning": True, "show_tool_use": False},
        "feishu": {"app_id": "fixture"},
    }))


def controller(home):
    ctrl = StreamCardController(home)
    ctrl._initialized = True
    ctrl._client = AsyncMock()
    ctrl._client.cardkit_create.side_effect = ["card-one", "card-two", "card-three"]
    ctrl._client.reply_card_by_id.side_effect = ["message-one", "message-two", "message-three"]
    ctrl._schedule_flush = lambda session: None
    return ctrl


async def create(ctrl, name):
    session = CardSession(name, "chat-fixture", asyncio.get_running_loop())
    ctrl._register_session(session, session_key=name)
    await ctrl._do_create_card(session)
    assert session.state == SessionState.STREAMING
    return session


@pytest.mark.asyncio
async def test_current_card_keeps_style_and_next_card_reads_file(tmp_path):
    write_config(tmp_path, body={"dark_color": "#222222", "text_size": "normal_v2"},
                 process_border=False, header={"template": "default"})
    ctrl = controller(tmp_path)
    first = await create(ctrl, "first")
    initial = deepcopy(ctrl._client.cardkit_create.call_args.args[0])
    write_config(tmp_path, body={"dark_color": "#252525", "text_size": "heading"},
                 process_border=True, header={"template": "blue"})
    first.segment_state.on_answer_delta("final answer")
    assert await ctrl._do_complete_card_inner(first)
    sealed = ctrl._client.cardkit_update.call_args.args[1]
    assert sealed["config"]["style"] == initial["config"]["style"]
    assert "template" not in sealed["header"]
    second = await create(ctrl, "second")
    fresh = ctrl._client.cardkit_create.call_args.args[0]
    assert fresh["config"]["style"]["color"]["cus-0"]["dark_mode"] == "rgba(37,37,37,1)"
    assert fresh["header"]["template"] == "blue"
    assert second.render_config.body_text_size == "heading"
    assert second.render_config.process_border is True


@pytest.mark.asyncio
async def test_disable_rejects_new_turn_but_finishes_owned_turn(tmp_path):
    write_config(tmp_path)
    ctrl = controller(tmp_path)
    session = await create(ctrl, "owned")
    write_config(tmp_path, enabled=False)
    assert not ctrl.enabled
    assert ctrl.on_message_started(message_id="new", chat_id="chat-fixture") is None
    assert ctrl.on_answer(message_id="owned", text="still delivered")
    assert ctrl.stream_card_healthy(message_id="owned")
    with patch("hermes_fry_cards.patch.get_controller", return_value=ctrl):
        assert await on_message_completed_wait(message_id="owned", answer="finished")
    assert session.state == SessionState.COMPLETED
    assert ctrl._client.cardkit_close_streaming.await_count >= 1


@pytest.mark.asyncio
async def test_disable_preserves_abort_and_blocks_interrupt_successor(tmp_path):
    write_config(tmp_path)
    ctrl = controller(tmp_path)
    session = await create(ctrl, "owned")
    write_config(tmp_path, enabled=False)
    ctrl._complete_session = lambda current: None
    ctrl.on_interrupted(old_message_id="owned", new_message_id="new", chat_id="chat-fixture")
    assert session.state == SessionState.ABORTED
    assert "new" not in ctrl._sessions


@pytest.mark.asyncio
async def test_rollover_captures_new_style_only_when_new_card_is_adopted(tmp_path):
    write_config(tmp_path, body={"dark_color": "#222222"})
    ctrl = controller(tmp_path)
    session = await create(ctrl, "owned")
    old_config = session.render_config
    write_config(tmp_path, body={"dark_color": "#252525"})
    new_ids = await ctrl._create_streaming_card(session)
    assert new_ids
    assert session.render_config is old_config
    assert session.pending_render_config.body_dark_color == "#252525"
    await ctrl._seal_current_card(session, [])
    old_sealed = ctrl._client.cardkit_update.call_args.args[1]
    assert old_sealed["config"]["style"]["color"]["cus-0"]["dark_mode"] == "rgba(34,34,34,1)"
    session.set_card(card_id=new_ids[0], card_msg_id=new_ids[1])
    assert session.render_config.body_dark_color == "#252525"


@pytest.mark.asyncio
async def test_live_throttle_keeps_adaptive_floor_and_one_timer():
    flush = FlushController(throttle_ms=0.5)
    flush.adapt_payload_size(50000, max_element_bytes=20000)
    flush.set_throttle(0.1)
    assert flush.throttle_ms == 2
    flush.set_card_message_ready(True)
    flush.schedule_update(AsyncMock())
    timer = flush._pending_timer
    flush.set_throttle(0.2)
    flush.schedule_update(AsyncMock())
    assert flush._pending_timer is timer
    assert flush.throttle_ms == 2
    flush.adapt_payload_size(100)
    assert flush.throttle_ms == 0.2
    flush.mark_completed()


@pytest.mark.asyncio
async def test_live_display_enable_adds_frame_before_writing(tmp_path):
    write_config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text())
    raw["display"]["show_reasoning"] = False
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw))
    ctrl = controller(tmp_path)
    session = await create(ctrl, "owned")
    assert session.process_panels == set()
    write_config(tmp_path)
    session.segment_state.on_reasoning_delta("now visible")
    await ctrl._do_flush(session)
    actions = ctrl._client.cardkit_batch_update.call_args_list[0].args[1]
    frame = actions[0]["params"]["elements"][0]
    assert frame["element_id"] == "unified_panel"
    assert "unified_panel" in session.process_panels
    assert not session.reasoning_timeline


def test_ttl_reads_same_config_instance_at_prune(tmp_path):
    write_config(tmp_path, card_ttl_sec=600)
    ctrl = controller(tmp_path)
    ctrl._prune_stale_sessions()
    assert ctrl._session_ttl == 600
    write_config(tmp_path, card_ttl_sec=120)
    ctrl._prune_stale_sessions()
    assert ctrl._session_ttl == 120


@pytest.mark.asyncio
async def test_cron_next_delivery_reads_header_theme(tmp_path):
    write_config(tmp_path, header={"template": "default"})
    ctrl = controller(tmp_path)
    await ctrl._do_cron_deliver("fixture-chat", "fixture content", task_name="fixture")
    assert "template" not in ctrl._client.send_card_to_chat.call_args.args[1]["header"]
    write_config(tmp_path, header={"template": "orange"})
    await ctrl._do_cron_deliver("fixture-chat", "fixture content", task_name="fixture")
    assert ctrl._client.send_card_to_chat.call_args.args[1]["header"]["template"] == "orange"


@pytest.mark.asyncio
async def test_disabled_hook_without_owner_does_not_deliver(tmp_path):
    from hermes_fry_cards.patch import on_answer_delta, on_session_aborted

    write_config(tmp_path, enabled=False)
    ctrl = controller(tmp_path)
    with patch("hermes_fry_cards.patch.get_controller", return_value=ctrl):
        assert not on_answer_delta(message_id="unknown", text="ignored")
        assert not await on_session_aborted(session_key="unknown")
        assert not await on_message_completed_wait(message_id="unknown", answer="fallback")
    ctrl._client.cardkit_create.assert_not_called()
    ctrl._client.send_card_to_chat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("admission", [{"enabled": False}, {"chat_types": []}])
async def test_explicit_unadmitted_turn_keeps_its_own_delivery(tmp_path, admission):
    write_config(tmp_path)
    ctrl = controller(tmp_path)
    owner = await create(ctrl, "owned")
    write_config(tmp_path, **admission)
    assert ctrl.on_message_started(
        message_id="new", chat_id="chat-fixture", chat_type="dm", session_key="owned"
    ) is None
    assert not ctrl.on_answer(message_id="new", session_key="owned", text="new answer")
    assert not ctrl.stream_card_healthy(message_id="new", session_key="owned")
    assert not await ctrl.on_completed_wait(
        message_id="new", session_key="owned", answer="new final"
    )
    assert owner.state == SessionState.STREAMING
    ctrl._client.cardkit_update.assert_not_called()
    ctrl._sessions["anchor"] = owner
    assert ctrl.on_answer(message_id="anchor", session_key="owned", text="owned answer")
    assert ctrl.on_answer(message_id=None, session_key="owned", text=" continuation")
    assert await ctrl.on_completed_wait(message_id=None, session_key="owned", answer="owned final")
    assert owner.state == SessionState.COMPLETED


def test_empty_chat_allowlist_applies_with_missing_type(tmp_path):
    write_config(tmp_path, chat_types=[])
    ctrl = controller(tmp_path)
    assert not ctrl._chat_type_allowed("")
    write_config(tmp_path, chat_types=["dm"])
    assert ctrl._chat_type_allowed("")


@pytest.mark.asyncio
async def test_empty_allowlist_blocks_static_cards_and_keeps_owned_receipts(tmp_path):
    write_config(tmp_path)
    ctrl = controller(tmp_path)
    await create(ctrl, "owned")
    write_config(tmp_path, chat_types=[])
    assert not await asyncio.to_thread(
        ctrl.on_cron_deliver, chat_id="other", content="cron", loop=None
    )
    assert not await ctrl.on_background_deliver(chat_id="other", preview="", content="done")
    assert not await ctrl.on_background_completion(chat_id="other", text="done", session_key="other")
    assert await ctrl.on_background_deliver(
        chat_id="chat-fixture", preview="", content="owned done", reply_to_message_id="owned"
    )
    assert await ctrl.on_background_completion(
        chat_id="chat-fixture", text="owned completion", session_key="owned"
    )
    ctrl._client.send_card_to_chat.assert_not_called()


@pytest.mark.asyncio
async def test_busy_notice_uses_active_card_for_queued_message(tmp_path):
    write_config(tmp_path)
    ctrl = controller(tmp_path)
    session = CardSession("owned", "chat-fixture", asyncio.get_running_loop())
    ctrl._register_session(session, session_key="owned")
    assert ctrl.on_busy_ack(message_id="queued", session_key="owned", text="queued receipt")
    assert session.segment_state.notices[0].text == "queued receipt"

