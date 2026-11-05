"""重定向 vs 真打断：旧轮完成事件归谁.

Hermes 对「回合进行中收到新消息」有两种判定：

- `redirected=True`（gateway.run_busy）：正在跑的这一轮被重定向到新消息，回复重新锚定
  到新消息，完成事件该落到新卡上 —— `_interrupt_map` 就是为这条路径存在的。
- `was_interrupted`（gateway.run_turn 的 注入点 9）：这一轮被真正打断，新消息自己另起
  一轮。Hermes 对打断回合的处置是 "Interrupted: discard the response"，旧轮尾巴不归新卡。

原来的实现不管哪一种都会建立 `_interrupt_map`，于是普通打断时旧轮的空/半截 final
会写进刚建好的新卡并封口，随后新消息的回合再建一张卡 —— 一条消息两张卡，其中一张是
空的 Done.。这里钉住修正后的归属。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from test_controller import _make_session, _setup_ctrl

from hermes_fry_cards.streaming.session import SessionState

BUSY_TEXT = "⏳ Queued for the next turn."


def _streaming(ctrl, message_id: str, *, key: str | None = None):
    session = _make_session(message_id)
    session.chat_id = "chat_1"
    session.state = SessionState.STREAMING
    session.card_id = f"card_{message_id}"
    session.card_msg_id = f"msg_{message_id}"
    ctrl._register_session(session, session_key=key)
    return session


def test_real_interrupt_keeps_old_tail_off_the_successor_card() -> None:
    """redirected=False：只写提示、不换卡；注入点 9 建新卡但不接管旧轮完成事件."""
    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_message_interrupted, on_turn_redirected

    ctrl = _setup_ctrl()
    old = _streaming(ctrl, "msg_old", key="key_1")
    old.state = SessionState.ABORTED  # 注入点 9 已经收过尾

    with patch.object(patch_mod, "get_controller", return_value=ctrl), \
            patch.object(ctrl, "_complete_session", lambda s: None):
        on_turn_redirected(
            new_message_id="msg_new",
            chat_id="chat_1",
            session_key="key_1",
            anchor_id="msg_new",
            redirected=False,
            text=BUSY_TEXT,
        )
        # 提示落在旧卡，新消息还没有卡
        assert "msg_new" not in ctrl._sessions

        on_message_interrupted(
            message_id="msg_old",
            new_message_id="msg_new",
            chat_id="chat_1",
            session_key="key_1",
        )

    # 新卡建好，但没有建立「旧轮完成 → 新卡」的映射
    assert "msg_new" in ctrl._sessions
    assert "msg_old" not in ctrl._interrupt_map
    assert "msg_new" not in ctrl._interrupt_map.values()
    assert "msg_old" in ctrl._interrupted_tails


@pytest.mark.asyncio
async def test_interrupted_tail_is_consumed_not_written_to_successor() -> None:
    """旧轮完成事件到达：吞掉（Hermes 也是 discard），新卡保持 STREAMING 等自己的回合."""
    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_message_interrupted

    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_old", key="key_1")
    ctrl._sessions["msg_old"].state = SessionState.ABORTED

    with patch.object(patch_mod, "get_controller", return_value=ctrl), \
            patch.object(ctrl, "_complete_session", lambda s: None):
        on_message_interrupted(
            message_id="msg_old",
            new_message_id="msg_new",
            chat_id="chat_1",
            session_key="key_1",
        )

    successor = ctrl._sessions["msg_new"]
    assert await ctrl.on_completed_wait(message_id="msg_old", answer="", session_key="key_1") is True
    # 新卡没被旧轮的 final 封掉，仍可接本轮真正的增量
    assert successor.state not in (SessionState.COMPLETED, SessionState.ABORTED)
    assert not [seg for seg in successor.segment_state.segments if seg.text]
    assert "msg_old" not in ctrl._interrupted_tails


def test_real_redirect_still_hands_completion_to_the_new_card() -> None:
    """redirected=True：这一轮没被打断，完成事件必须落到新卡（原有行为保持）."""
    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_turn_redirected

    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_old", key="key_1")

    with patch.object(patch_mod, "get_controller", return_value=ctrl), \
            patch.object(ctrl, "_complete_session", lambda s: None):
        on_turn_redirected(
            new_message_id="msg_new",
            chat_id="chat_1",
            session_key="key_1",
            anchor_id="msg_new",
            redirected=True,
            text="",
        )

    assert ctrl._interrupt_map["msg_old"] == "msg_new"
    assert ctrl._completion_session("msg_old", "key_1") is ctrl._sessions["msg_new"]
    assert "msg_old" not in ctrl._interrupted_tails
