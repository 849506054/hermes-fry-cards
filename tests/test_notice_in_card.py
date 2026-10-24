"""Issue #43 — 提示消息入卡（忙碌回执 / 重定向 / 后台回执）.

Hermes 在回合进行中会自己合成状态提示（"⏳ Queued for the next turn…" 等）。
本模块钉住重写后的行为：提示写进**当前活跃卡**的通知槽，随完成态卡片一起
呈现，插件不另写文案；卡片归属沿用现有 ``_sessions`` / ``_session_keys`` /
``_interrupt_map``，不做归属重构。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from test_controller import _make_session, _mock_client, _setup_ctrl

from hermes_fry_cards.cardkit.builder import build_complete_card, build_notice_element
from hermes_fry_cards.streaming.segments import NotificationSlot, SegmentState
from hermes_fry_cards.streaming.session import SessionState

BUSY_TEXT = "⏳ Queued for the next turn. I'll respond once the current task finishes."
REDIRECT_TEXT = "↪ Steered into current run"


def _streaming(ctrl, message_id: str, *, chat_id: str = "chat_1", key: str | None = None):
    session = _make_session(message_id)
    session.chat_id = chat_id
    session.state = SessionState.STREAMING
    session.card_id = f"card_{message_id}"
    session.card_msg_id = f"msg_{message_id}"
    ctrl._register_session(session, session_key=key)
    return session


# ── 通知槽（数据层） ────────────────────────────────────────────────────────


def test_add_notice_dedupes_and_ignores_blank() -> None:
    state = SegmentState()
    state.add_notice(BUSY_TEXT)
    state.add_notice(f"  {BUSY_TEXT}  ")
    state.add_notice("   ")
    assert [slot.text for slot in state.notices] == [BUSY_TEXT]
    assert isinstance(state.notices[0], NotificationSlot)


def test_complete_card_renders_notices_above_body() -> None:
    card = build_complete_card(segments=[], all_tool_steps=[], notices=[BUSY_TEXT, REDIRECT_TEXT])
    elements = card["body"]["elements"]
    assert elements[0]["element_id"] == "fry_notice"
    assert elements[0]["content"] == BUSY_TEXT
    assert elements[1]["content"] == REDIRECT_TEXT


def test_complete_card_without_notices_has_no_notice_elements() -> None:
    card = build_complete_card(segments=[], all_tool_steps=[])
    assert not [el for el in card["body"]["elements"] if el.get("element_id") == "fry_notice"]


def test_notice_element_renders_raw_text_without_label() -> None:
    """#43 反馈：重定向/忙碌文案本身是完整一句话，不再套「💡 提示」标签."""
    element = build_notice_element(REDIRECT_TEXT)
    assert element["content"] == REDIRECT_TEXT
    assert "💡" not in element["content"]
    assert "提示" not in element["content"]


# ── 忙碌回执 ────────────────────────────────────────────────────────────────


def test_busy_ack_goes_into_current_card() -> None:
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_busy", key="key_busy")

    assert ctrl.on_busy_ack(text=BUSY_TEXT, message_id="msg_busy", session_key="key_busy") is True
    assert [slot.text for slot in session.segment_state.notices] == [BUSY_TEXT]


def test_busy_ack_falls_back_by_session_key_without_message_id() -> None:
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_busy_key", key="key_only")

    assert ctrl.on_busy_ack(text=BUSY_TEXT, session_key="key_only") is True
    assert session.segment_state.notices


def test_busy_ack_without_owner_is_not_consumed() -> None:
    ctrl = _setup_ctrl()
    assert ctrl.on_busy_ack(text=BUSY_TEXT, message_id="msg_unknown", session_key="key_unknown") is False


def test_busy_ack_ambiguous_without_owner_is_not_consumed() -> None:
    """多张活跃卡且无标识：宁可走 Hermes 原发送，也不误写别人的卡."""
    ctrl = _setup_ctrl()
    first = _streaming(ctrl, "msg_a")
    second = _streaming(ctrl, "msg_b")
    assert ctrl.on_busy_ack(text=BUSY_TEXT) is False
    assert not first.segment_state.notices
    assert not second.segment_state.notices


def test_busy_ack_single_live_card_is_used() -> None:
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_solo")
    assert ctrl.on_busy_ack(text=BUSY_TEXT) is True
    assert session.segment_state.notices


def test_busy_ack_ignores_blank_text() -> None:
    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_blank")
    assert ctrl.on_busy_ack(text="   ") is False


# ── 重定向 ──────────────────────────────────────────────────────────────────


def test_redirect_closes_old_card_and_opens_new_one() -> None:
    ctrl = _setup_ctrl()
    old = _streaming(ctrl, "msg_old", key="key_turn")
    ctrl._complete_session = lambda s: s.__setattr__("state", SessionState.ABORTED)  # type: ignore[method-assign]

    ctrl.on_interrupted(
        old_message_id="msg_old",
        new_message_id="msg_new",
        chat_id="chat_1",
        session_key="key_turn",
    )
    new = ctrl._sessions["msg_new"]
    new.segment_state = SegmentState()
    ctrl.on_redirected_notice(text=REDIRECT_TEXT, message_id="msg_new", session_key="key_turn")

    assert old.state == SessionState.ABORTED
    assert [slot.text for slot in new.segment_state.notices] == [REDIRECT_TEXT]
    # 完成回调仍按 _interrupt_map 落到新卡
    assert ctrl._completion_session("msg_old") is new


def test_redirect_notice_before_new_card_arrives_is_applied_on_register() -> None:
    """提示先于新卡登记到达（重定向开口与建卡并发）：注册时补写."""
    ctrl = _setup_ctrl()
    running = _streaming(ctrl, "msg_running", key="key_pending")
    ctrl._complete_session = lambda s: s.__setattr__("state", SessionState.ABORTED)  # type: ignore[method-assign]
    # 模拟换卡开口：旧卡已收尾但 session_key 尚未指向新卡（新卡还没登记）
    running.state = SessionState.ABORTED
    ctrl._session_keys.pop("key_pending", None)

    assert ctrl.on_redirected_notice(text=REDIRECT_TEXT, session_key="key_pending") is True
    assert ctrl._pending_notices["key_pending"] == [REDIRECT_TEXT]

    new = _streaming(ctrl, "msg_pending_new", key="key_pending")
    assert [slot.text for slot in new.segment_state.notices] == [REDIRECT_TEXT]
    assert "key_pending" not in ctrl._pending_notices


def test_session_key_takeover_aborts_previous_card() -> None:
    """同一会话键下的新卡接管：旧卡必须收尾，否则完成回调落到已封的卡上."""
    ctrl = _setup_ctrl()
    closed: list[str] = []
    ctrl._complete_session = lambda s: closed.append(s.message_id)  # type: ignore[method-assign]
    _streaming(ctrl, "msg_prev", key="key_takeover")

    new = _streaming(ctrl, "msg_next", key="key_takeover")
    assert closed == ["msg_prev"]
    assert ctrl._session_keys["key_takeover"] is new


# ── 后台回执 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_background_receipt_merges_into_live_card() -> None:
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_bg", chat_id="chat_bg")

    delivered = await ctrl.on_background_deliver(
        chat_id="chat_bg",
        preview="done",
        content="后台任务完成",
        reply_to_message_id="msg_bg",
    )
    assert delivered is True
    assert [slot.text for slot in session.segment_state.notices] == ["后台任务完成"]


@pytest.mark.asyncio
async def test_background_receipt_without_live_card_still_sends_card() -> None:
    ctrl = _setup_ctrl()
    ctrl._do_background_deliver = AsyncMock()  # type: ignore[method-assign]

    delivered = await ctrl.on_background_deliver(
        chat_id="chat_bg",
        preview="done",
        content="任务完成",
        reply_to_message_id="msg_gone",
    )
    assert delivered is True
    ctrl._do_background_deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_receipt_only_merges_into_same_chat() -> None:
    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_other_chat", chat_id="chat_other")
    ctrl._do_background_deliver = AsyncMock()  # type: ignore[method-assign]

    await ctrl.on_background_deliver(
        chat_id="chat_target",
        preview="done",
        content="任务完成",
        reply_to_message_id="msg_other_chat",
    )
    assert ctrl._do_background_deliver.await_count == 1


# ── 完成态卡片带上提示 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_completed_card_includes_notice_slot() -> None:
    """进行中的提示（忙碌回执）落完成态卡片底部（footer 之前，#43 反馈）."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_done", key="key_done")
    session.segment_state.on_answer_delta("answer text")
    session.segment_state.add_notice(BUSY_TEXT)
    session.create_task = None
    session.card_id = "card_done"
    session.state = SessionState.STREAMING

    ok = await ctrl._do_complete_card(session)
    assert ok is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    bodies = card["body"]["elements"]
    notice_positions = [i for i, el in enumerate(bodies) if el.get("element_id") == "fry_notice"]
    assert notice_positions, "提示必须出现在完成态卡片上"
    # 底部通知位：在答案正文之后（不在卡顶）
    assert notice_positions[0] > 0
    assert bodies[notice_positions[0]]["content"] == BUSY_TEXT


def test_streaming_card_carries_latest_notice_after_redirect() -> None:
    """重定向后新卡建立时带上提示；提示落卡片底部（loading 之前，#43 反馈）."""
    from hermes_fry_cards.cardkit.builder import build_streaming_card_v2
    from hermes_fry_cards.streaming.controller import _latest_notice

    session = _make_session("msg_streaming_notice")
    session.segment_state.add_notice(REDIRECT_TEXT)
    assert _latest_notice(session) == REDIRECT_TEXT

    card = build_streaming_card_v2(notice=_latest_notice(session))
    elements = card["body"]["elements"]
    assert elements[-1]["element_id"] == "loading_icon"
    assert elements[-2]["element_id"] == "fry_notice"
    assert elements[-2]["content"] == REDIRECT_TEXT
    # 不在卡片顶部（工具输出之上）
    assert elements[0]["element_id"] != "fry_notice"


# ── 通知区元素记账（#43 评审） ──────────────────────────────────────────────


def test_complete_card_notice_ids_are_unique() -> None:
    """一张卡命中多条提示时，元素 id 必须唯一（固定 fry_notice 会撞 id）."""
    card = build_complete_card(segments=[], all_tool_steps=[], notices=[BUSY_TEXT, REDIRECT_TEXT])
    ids = [el["element_id"] for el in card["body"]["elements"] if el.get("element_id", "").startswith("fry_notice")]
    assert ids == ["fry_notice", "fry_notice_1"]
    assert len(ids) == len(set(ids))


def test_three_notices_have_three_distinct_ids() -> None:
    from hermes_fry_cards.cardkit.builder import build_notice_elements

    elements = build_notice_elements(["a", "b", "c"])
    assert [e["element_id"] for e in elements] == ["fry_notice", "fry_notice_1", "fry_notice_2"]


def test_notice_elements_are_counted_in_element_budget() -> None:
    """每条提示占一个元素，必须计入 element_count，否则接近上限时会少算."""
    from hermes_fry_cards.streaming.controller import _sync_notice_elements

    session = _make_session("msg_notice_budget")
    # 已建卡：loading 元素已在计数器里
    session.element_count = 10
    session.notice_element_count = 0
    session.segment_state.add_notice(BUSY_TEXT)
    session.segment_state.add_notice(REDIRECT_TEXT)

    notices = _sync_notice_elements(session)
    assert notices == [BUSY_TEXT, REDIRECT_TEXT]
    assert session.notice_element_count == 2
    assert session.element_count == 12


def test_notice_element_sync_is_idempotent() -> None:
    from hermes_fry_cards.streaming.controller import _sync_notice_elements

    session = _make_session("msg_notice_idem")
    session.element_count = 5
    session.notice_element_count = 0
    session.segment_state.add_notice(BUSY_TEXT)

    _sync_notice_elements(session)
    _sync_notice_elements(session)
    assert session.element_count == 6
    assert session.notice_element_count == 1


@pytest.mark.asyncio
async def test_completed_card_element_count_matches_rendered_elements() -> None:
    """完成态：记账的元素数不小于卡片实际元素数（含通知区）."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_notice_card", key="key_notice_card")
    session.segment_state.on_answer_delta("answer text")
    session.segment_state.add_notice(BUSY_TEXT)
    session.segment_state.add_notice(REDIRECT_TEXT)
    session.create_task = None
    session.card_id = "card_notice_card"
    # 已建卡：loading 元素已有基线，尚未给提示记账
    session.element_count = 1
    session.notice_element_count = 0

    assert await ctrl._do_complete_card(session) is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    rendered = [
        el for el in card["body"]["elements"] if str(el.get("element_id", "")).startswith("fry_notice")
    ]
    assert len(rendered) == 2
    assert session.element_count >= len(card["body"]["elements"])


# ── 压缩封卡与别名兼容（#43 增强） ──────────────────────────────────────


def test_on_turn_redirected_accepts_message_id_alias() -> None:
    """镜像烘入的模板调用 on_turn_redirected(session_key=..., message_id=...) 必须正常工作."""
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_turn_redirected

    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_old", key="key_alias")
    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        # 即使只有 session_key 和 message_id，也不应抛 TypeError
        on_turn_redirected(session_key="key_alias", message_id="msg_new")
    assert "msg_new" in ctrl._sessions


def test_on_busy_ack_accepts_ctrl_keyword() -> None:
    """构建模板传或不传 ctrl 都不应抛 TypeError."""
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_busy_ack

    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_b1", key="key_b1")
    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        assert on_busy_ack(ctrl=ctrl, text=BUSY_TEXT, session_key="key_b1") is True
        assert on_busy_ack(text=BUSY_TEXT, session_key="key_b1") is True


@pytest.mark.asyncio
async def test_session_compressed_seals_active_card() -> None:
    """触发压缩时封卡：把当前会话活跃卡片封死为完成态."""
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_comp", key="key_comp")
    session.card_id = "card_comp"
    sealed = []
    ctrl._complete_session_after_creation = AsyncMock(side_effect=lambda s: sealed.append(s.card_id) or True)

    assert await ctrl.on_session_compressed(session_key="key_comp") is True
    assert session.state == SessionState.COMPLETED
    assert sealed == ["card_comp"]


def test_on_compression_sync_entry() -> None:
    """同步 entry 调用 on_compression."""
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_sync", key="key_sync")
    session.card_id = "card_sync"
    closed = []
    ctrl._complete_session = lambda s: closed.append(s.card_id)

    assert ctrl.on_compression(session_key="key_sync") is True
    assert session.state == SessionState.COMPLETED
    assert closed == ["card_sync"]


def test_on_session_compressed_skips_when_no_active_session() -> None:
    ctrl = _setup_ctrl()
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_session_compressed

    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        assert on_session_compressed(session_key="non_existent") is False


# ── 提示分区：进行中提示落底部，后台回执在顶部（#43 反馈） ──────────────────


def test_notice_kinds_split_bottom_and_top() -> None:
    """忙碌/重定向是进行中提示（底部），后台回执是完成后通知（顶部）."""
    state = SegmentState()
    state.add_notice(BUSY_TEXT, kind="busy")
    state.add_notice(REDIRECT_TEXT, kind="redirect")
    state.add_notice("后台任务完成", kind="background")

    top, bottom = state.split_notices()
    assert top == ["后台任务完成"]
    assert bottom == [BUSY_TEXT, REDIRECT_TEXT]


def test_complete_card_renders_progress_notices_at_bottom() -> None:
    card = build_complete_card(
        segments=[],
        all_tool_steps=[],
        notices=["后台任务完成"],
        progress_notices=[BUSY_TEXT, REDIRECT_TEXT],
    )
    elements = card["body"]["elements"]
    ids = [el.get("element_id") for el in elements]
    assert ids.index("fry_notice") < ids.index("fry_notice_1") < ids.index("fry_notice_2")
    assert elements[ids.index("fry_notice")]["content"] == "后台任务完成"
    assert elements[ids.index("fry_notice_1")]["content"] == BUSY_TEXT
    assert elements[ids.index("fry_notice_2")]["content"] == REDIRECT_TEXT
    # 底部通知位在顶部回执之后，且都在卡内（无 id 冲突；正文/表格元素无 id，不计）
    notice_ids = [i for i in ids if i]
    assert len(set(notice_ids)) == len(notice_ids)


@pytest.mark.asyncio
async def test_busy_receipt_lands_at_bottom_of_completed_card() -> None:
    """忙碌回执的 kind=busy：完成态重建时落底部通知位."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_kind_busy", key="key_kind_busy")
    session.segment_state.on_answer_delta("answer text")
    ctrl.on_busy_ack(text=BUSY_TEXT, message_id="msg_kind_busy", session_key="key_kind_busy")
    session.create_task = None
    session.card_id = "card_kind_busy"
    session.state = SessionState.STREAMING

    assert await ctrl._do_complete_card(session) is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    bodies = card["body"]["elements"]
    notice = next(el for el in bodies if el.get("element_id") == "fry_notice")
    assert notice["content"] == BUSY_TEXT
    # 不在正文最顶上（答案之后才是通知位）
    assert bodies[0].get("element_id") != "fry_notice"


@pytest.mark.asyncio
async def test_background_receipt_stays_at_top_of_completed_card() -> None:
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_kind_bg", key="key_kind_bg")
    session.segment_state.on_answer_delta("answer text")
    session.segment_state.add_notice("后台任务完成", kind="background")
    session.create_task = None
    session.card_id = "card_kind_bg"
    session.state = SessionState.STREAMING

    assert await ctrl._do_complete_card(session) is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert card["body"]["elements"][0]["content"] == "后台任务完成"


# ── 重定向封卡清提示（#43 反馈：同一句话不在旧卡正文复述） ──────────────────


def test_interrupt_clears_progress_notices_on_old_card() -> None:
    ctrl = _setup_ctrl()
    old = _streaming(ctrl, "msg_old_notice", key="key_old_notice")
    old.segment_state.add_notice(BUSY_TEXT, kind="busy")
    old.segment_state.add_notice("后台任务完成", kind="background")
    ctrl._complete_session = lambda s: s.__setattr__("state", SessionState.ABORTED)  # type: ignore[method-assign]

    ctrl.on_interrupted(
        old_message_id="msg_old_notice",
        new_message_id="msg_new_notice",
        chat_id="chat_1",
        session_key="key_old_notice",
    )
    # 进行中提示随新卡走，旧卡只保留完成后的回执
    assert [slot.text for slot in old.segment_state.notices] == ["后台任务完成"]


def test_clear_progress_notices_keeps_background_receipts() -> None:
    session = _make_session("msg_clear")
    session.segment_state.add_notice(BUSY_TEXT, kind="busy")
    session.segment_state.add_notice("后台任务完成", kind="background")

    session.clear_progress_notices()
    assert [slot.text for slot in session.segment_state.notices] == ["后台任务完成"]


# ── 合成轮次 id（后台完成回执的 ["completion", ...] 序列，#43 反馈） ─────────


def test_synthetic_completion_id_creates_send_to_chat_session() -> None:
    """合成 id 不是飞书 open_message_id：建会话但卡片直接投聊天，不 reply."""
    ctrl = _setup_ctrl()
    synthetic_id = '["completion","proc_684099a22fe4",1792773079.3370965]'

    started = ctrl.on_message_started(
        message_id=synthetic_id,
        chat_id="chat_bg",
        session_key="key_syn",
    )
    assert started == synthetic_id
    session = ctrl._sessions[synthetic_id]
    assert session.synthetic is True
    assert session.anchor_id is None


def test_synthetic_completion_id_is_detected() -> None:
    from hermes_fry_cards.controller import _is_synthetic_turn_id

    assert _is_synthetic_turn_id('["completion","proc_x",1.0]') is True
    assert _is_synthetic_turn_id('["background","task",1.0]') is True
    assert _is_synthetic_turn_id("om_x100b628cf392e4a") is False
    assert _is_synthetic_turn_id("syn-abc123") is False


# ── 网关流消费者投递标记（#43 反馈：streamed=False 重复投递警告） ────────────


def test_mark_stream_delivered_reports_healthy_card() -> None:
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import mark_stream_delivered

    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_mark", key="key_mark")

    class _Consumer:
        pass

    consumer = _Consumer()
    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        mark_stream_delivered(consumer, "msg_mark")
        assert consumer.has_durably_delivered_text("final answer") is True

        session = ctrl._sessions["msg_mark"]
        session.state = SessionState.FAILED
        assert consumer.has_durably_delivered_text("final answer") is False


def test_mark_stream_delivered_survives_card_loss() -> None:
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import mark_stream_delivered

    ctrl = _setup_ctrl()

    class _Consumer:
        pass

    consumer = _Consumer()
    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        mark_stream_delivered(consumer, "msg_gone")
        assert consumer.has_durably_delivered_text("final answer") is False


def test_mark_stream_delivered_ignores_missing_consumer() -> None:
    from hermes_fry_cards.patch import mark_stream_delivered

    mark_stream_delivered(None, "msg_any")  # 不抛即通过


# ── 连续第二次打断：REDIRECT_RESOLVED 锚点在回执防抖之前换卡（#43 反馈） ────


def test_on_turn_redirected_with_empty_text_still_switches_cards() -> None:
    """防抖场景没有提示文案：redirected=True 也要完成换卡."""
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_turn_redirected

    ctrl = _setup_ctrl()
    old = _streaming(ctrl, "msg_old_2nd", key="key_2nd")
    ctrl._complete_session = lambda s: s.__setattr__("state", SessionState.ABORTED)  # type: ignore[method-assign]

    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        on_turn_redirected(
            new_message_id="msg_new_2nd",
            chat_id="chat_1",
            session_key="key_2nd",
            redirected=True,
            text="",
        )
    assert old.state == SessionState.ABORTED
    assert "msg_new_2nd" in ctrl._sessions
