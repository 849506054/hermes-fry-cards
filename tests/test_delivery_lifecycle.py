"""投递生命周期归属、切换和首渲染契约。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from hermes_fry_cards.cardkit.builder import build_background_card
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.feishu import FeishuClient
from hermes_fry_cards.streaming.session import SessionState


@pytest.fixture
def ctrl(tmp_path):
    controller = StreamCardController(tmp_path)
    controller._cfg._raw = {
        "streaming": {"enabled": True},
        "feishu": {"app_id": "app", "app_secret": "secret"},
    }
    controller._initialized = True
    controller._client = AsyncMock(spec=FeishuClient)
    controller._client.cardkit_create.side_effect = [f"card-{i}" for i in range(20)]
    controller._client.reply_card_by_id.side_effect = [f"reply-{i}" for i in range(20)]
    controller._client.send_card_to_chat.side_effect = [f"sent-{i}" for i in range(20)]
    return controller


def register(ctrl, message_id="A", key="key", anchor_id=None):
    with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
        result = ctrl.on_message_started(message_id=message_id, chat_id="chat", session_key=key, anchor_id=anchor_id)
    return ctrl._sessions[result]


@pytest.mark.asyncio
async def test_explicit_unknown_never_uses_current(ctrl):
    current = register(ctrl)
    assert ctrl.on_answer(message_id="unknown", session_key="key", text="wrong") is False
    assert ctrl.resolve_session_id("unknown", "key") is None
    assert await ctrl.on_completed_wait(message_id="unknown", session_key="key", answer="wrong") is False
    assert current.segment_state.segments == []
    assert not current.flush.completed


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [SessionState.ABORTED, SessionState.COMPLETED])
async def test_closed_and_disposed_stream_is_consumed_without_writing(ctrl, state):
    old = register(ctrl, anchor_id="alias")
    old.state = state
    current = register(ctrl, "B")
    ctrl._dispose_session(old)
    for message_id in ("A", "alias"):
        assert ctrl.on_answer(message_id=message_id, session_key="key", text="late") is True
        assert ctrl.on_tool_update(message_id=message_id, session_key="key", tool_name="read", status="started") is True
        assert await ctrl.on_completed_wait(message_id=message_id, session_key="key", answer="late") is True
    assert current.segment_state.segments == []
    assert not current.flush.completed


@pytest.mark.asyncio
async def test_failed_and_disposed_deliveries_allow_real_text(ctrl):
    old = register(ctrl)
    old.mark_failed("create failed")
    assert ctrl.on_answer(message_id="A", session_key="key", text="real") is False
    assert await ctrl.on_completed_wait(message_id="A", session_key="key", answer="real") is False
    assert ctrl.consume_text_fallback("A") is True
    current = register(ctrl, "B")
    assert ctrl.on_answer(message_id="A", session_key="key", text="real") is False
    assert await ctrl.on_completed_wait(message_id="A", session_key="key", answer="real") is False
    assert current.segment_state.segments == []


@pytest.mark.asyncio
async def test_no_id_start_reuses_creating_and_streaming_card(ctrl):
    current = register(ctrl)
    current.state = SessionState.CREATING
    assert ctrl.on_message_started(message_id=None, chat_id="chat", session_key="key") == "A"
    assert ctrl.on_answer(message_id=None, session_key="key", text="continuation") is True
    current.state = SessionState.STREAMING
    assert ctrl.on_message_started(message_id=None, chat_id="chat", session_key="key") == "A"
    assert ctrl._session_keys["key"] is current


@pytest.mark.asyncio
async def test_new_start_closes_previous_and_same_anchor_reuses(ctrl):
    old = register(ctrl, anchor_id="thread")
    assert ctrl.on_message_started(message_id="A2", anchor_id="thread", chat_id="chat", session_key="key") == "A"
    assert ctrl._sessions["A2"] is old
    new = register(ctrl, "B")
    assert old.flush.completed
    assert old.state == SessionState.ABORTED
    assert ctrl._session_keys["key"] is new
    assert not new.flush.completed


@pytest.mark.asyncio
async def test_redirect_chain_creation_and_native_notice(ctrl):
    register(ctrl)
    with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
        ctrl.on_turn_redirected(session_key="key", message_id="B")
        b = ctrl._session_keys["key"]
        ctrl.on_turn_redirected(session_key="key", message_id="B")
        assert ctrl._session_keys["key"] is b
        ctrl.on_turn_redirected(session_key="key", message_id="C")
    c = ctrl._session_keys["key"]
    assert b.flush.completed
    assert c.message_id == "C"
    # 重定向只换卡，不写提示语；文案由 Hermes 的 busy-ack 原样带进来。
    assert c.segment_state.segments == []
    await ctrl._do_create_card(c)
    first_card = ctrl._client.cardkit_create.await_args.args[0]
    ids = [el.get("element_id") for el in first_card["body"]["elements"]]
    assert "sticky_notice" not in ids
    assert ctrl.on_busy_ack(session_key="key", text="busy") is True
    assert (
        await ctrl.on_background_deliver(chat_id="chat", preview="preview", content="receipt", reply_to_message_id="C")
        is True
    )
    assert ctrl.on_answer(message_id="C", text="answer") is True
    assert await ctrl.on_completed_wait(message_id="A", session_key="key", answer="late A") is True
    assert await ctrl.on_completed_wait(message_id="B", session_key="key", answer="late B") is True
    assert not c.flush.completed
    assert await ctrl.on_completed_wait(message_id="C", answer="answer") is True
    card = ctrl._client.cardkit_update.await_args.args[1]
    texts = [el.get("content") for el in card["body"]["elements"] if el.get("tag") == "markdown"]
    assert texts[:2] == ["receipt", "answer"]
    assert "↪ 已重定向当前运行" not in json.dumps(card, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_id,chat", [(None, "chat"), ("missing", "chat"), ("A", "other-chat")])
async def test_background_receipt_requires_matching_chat_and_anchor(ctrl, reply_id, chat):
    current = register(ctrl)
    await ctrl._do_create_card(current)
    assert await ctrl.on_background_deliver(
        chat_id=chat, preview="summary-only", content="receipt", reply_to_message_id=reply_id
    )
    assert current.segment_state.segments == []
    card = ctrl._client.send_card_to_chat.await_args.args[1]
    assert "header" not in card
    assert card["config"]["summary"]["content"] == "summary-only"
    assert "summary-only" not in json.dumps(card["body"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_completion_gate_rejects_notices_and_late_delta(ctrl):
    current = register(ctrl)
    await ctrl._do_create_card(current)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = ctrl._do_complete_card_inner

    async def delayed(session):
        entered.set()
        await release.wait()
        return await original(session)

    with patch.object(ctrl, "_do_complete_card_inner", side_effect=delayed):
        waiter = asyncio.create_task(ctrl.on_completed_wait(message_id="A", answer="answer"))
        await entered.wait()
        assert ctrl.on_busy_ack(session_key="key", text="busy") is False
        assert ctrl.on_answer(message_id="A", text="late") is True
        assert await ctrl.on_background_deliver(
            chat_id="chat", preview="summary", content="receipt", reply_to_message_id="A"
        )
        assert ctrl._client.send_card_to_chat.await_count == 1
        release.set()
        assert await waiter
    assert [s.text for s in current.segment_state.segments] == ["answer"]


def test_background_builder_summary_only():
    card = build_background_card("summary", "正文")
    assert "header" not in card
    assert card["config"]["summary"]["content"] == "summary"
    assert card["body"]["elements"][0]["content"] == "正文"


@pytest.mark.asyncio
async def test_redirect_transfers_while_old_creation_is_in_flight(ctrl):
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def create(card):
        if not entered.is_set():
            entered.set()
            await gate.wait()
            return "old-card"
        return "new-card"

    ctrl._client.cardkit_create.side_effect = create
    assert ctrl.on_message_started(message_id="A", chat_id="chat", session_key="key") == "A"
    old = ctrl._sessions["A"]
    await entered.wait()
    assert old.state == SessionState.CREATING
    ctrl.on_turn_redirected(session_key="key", message_id="B")
    new = ctrl._sessions["B"]
    assert not old.writable
    assert new.writable
    assert ctrl.on_answer(message_id="A", text="old late") is True
    assert ctrl.on_answer(message_id="B", text="new answer") is True
    gate.set()
    await old.create_task
    await new.create_task
    await asyncio.sleep(0.05)
    assert not old.writable
    assert old.state == SessionState.ABORTED
    assert ctrl._session_keys["key"] is new
    assert old.card_msg_id not in ctrl._read_live_cards()
    assert await ctrl.on_completed_wait(message_id="B", answer="new answer")


@pytest.mark.asyncio
async def test_tombstones_expire_without_falling_back_to_current(ctrl):
    old = register(ctrl, anchor_id="alias")
    old.state = SessionState.ABORTED
    ctrl._dispose_session(old)
    current = register(ctrl, "B")
    ctrl._closed_ids = {key: (kind, 0) for key, (kind, _) in ctrl._closed_ids.items()}
    assert ctrl.on_answer(message_id="A", session_key="key", text="late") is False
    assert ctrl.resolve_session_id("alias", "key") is None
    assert ctrl._closed_ids == {}
    assert current.segment_state.segments == []


@pytest.mark.asyncio
async def test_boundary_then_no_id_start_creates_synthetic(ctrl):
    old = register(ctrl)
    await ctrl._do_create_card(old)
    assert await ctrl.on_completed_wait(message_id="A", answer="first answer")
    with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
        new_id = ctrl.on_message_started(message_id=None, chat_id="chat", session_key="key")
    assert new_id.startswith("syn-")
    assert ctrl._sessions[new_id].synthetic
    assert ctrl._sessions[new_id] is not old


@pytest.mark.asyncio
async def test_merged_background_notice_uses_clean_delivered_text(ctrl):
    current = register(ctrl)
    await ctrl._do_create_card(current)
    assert await ctrl.on_background_deliver(
        chat_id="chat",
        preview="summary",
        content="<think>secret</think>receipt",
        reply_to_message_id="A",
    )
    assert current.segment_state.segments[0].text == "receipt"
    assert await ctrl.on_completed_wait(message_id="A", answer="done")


@pytest.mark.asyncio
async def test_completion_waits_for_already_scheduled_flush_task(ctrl):
    current = register(ctrl)
    await ctrl._do_create_card(current)
    invoked = []
    current.flush._do_flush_task(lambda: asyncio.sleep(0))
    current.flush.mark_completed()
    await asyncio.wait_for(current.flush.wait_for_flush(), 1)
    assert invoked == []
    assert await ctrl._do_complete_card(current)


@pytest.mark.asyncio
async def test_background_notice_streams_before_existing_answer(ctrl):
    current = register(ctrl)
    await ctrl._do_create_card(current)
    assert ctrl.on_answer(message_id="A", text="answer")
    await ctrl._do_flush(current)
    assert await ctrl.on_background_deliver(
        chat_id="chat",
        preview="summary",
        content="receipt",
        reply_to_message_id="A",
    )
    await ctrl._do_flush(current)
    actions = ctrl._client.cardkit_batch_update.await_args.args[1]
    addition = next(action for action in actions if action["action"] == "add_elements")
    assert addition["params"]["type"] == "insert_before"
    assert addition["params"]["target_element_id"] == "answer_0"
    assert addition["params"]["elements"][0]["element_id"] == "notice"
    assert any(call.args[1:3] == ("notice", "receipt") for call in ctrl._client.cardkit_stream_element.await_args_list)
    assert await ctrl.on_completed_wait(message_id="A", answer="answer")
