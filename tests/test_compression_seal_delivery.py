"""压缩封卡与收尾钩子的先后顺序（漏卡修复的回归钉）.

现象：上下文压缩在收尾钩子之前封卡，会话被标成 COMPLETED；钩子随后查不到会话，
返回「未投递」，Hermes 把同一份回复再按纯文本发一遍 —— 卡片 + 纯文本两份。
修复：压缩时打 ``compression_sealed`` 标记，收尾钩子比对卡内正文与最终答案，
一致即视为已投递（返回 True，抑制纯文本补发）；不一致仍返回 False。
"""
from __future__ import annotations

import pytest
from test_controller import _make_session, _setup_ctrl

from hermes_fry_cards.streaming.session import SessionState


def _sealed_session(ctrl, message_id: str = "msg_seal", key: str = "key_seal"):
    session = _make_session(message_id)
    session.state = SessionState.STREAMING
    session.card_id = f"card_{message_id}"
    session.card_msg_id = f"msg_{message_id}"
    ctrl._register_session(session, session_key=key)
    return session


def test_compression_marks_session_sealed() -> None:
    ctrl = _setup_ctrl()
    session = _sealed_session(ctrl)
    assert session.compression_sealed is False
    assert ctrl.on_compression(session_key="key_seal") is True
    assert session.compression_sealed is True
    assert session.state == SessionState.COMPLETED


@pytest.mark.asyncio
async def test_completion_after_compression_suppresses_plain_send() -> None:
    """压缩已把同一段正文写进卡：收尾钩子报「已投递」，网关不再补发纯文本."""
    ctrl = _setup_ctrl()
    session = _sealed_session(ctrl)
    session.segment_state.on_answer_delta("验过了。热生效是真的。")
    ctrl.on_compression(session_key="key_seal")

    assert await ctrl.on_completed_wait(
        message_id="msg_seal", answer="验过了。热生效是真的。", session_key="key_seal",
    ) is True


@pytest.mark.asyncio
async def test_completion_after_compression_with_partial_body_does_not_suppress() -> None:
    """卡里只有半截预览时不能抑制，否则最终答案就丢了."""
    ctrl = _setup_ctrl()
    session = _sealed_session(ctrl, "msg_half", "key_half")
    session.segment_state.on_answer_delta("验过了")
    ctrl.on_compression(session_key="key_half")

    assert await ctrl.on_completed_wait(
        message_id="msg_half", answer="验过了。热生效是真的。完整的一段答复。", session_key="key_half",
    ) is False


@pytest.mark.asyncio
async def test_completion_on_unsealed_terminal_session_still_yields() -> None:
    """没有压缩标记的已完成会话维持原行为（交回网关）."""
    ctrl = _setup_ctrl()
    session = _sealed_session(ctrl, "msg_plain", "key_plain")
    session.state = SessionState.COMPLETED
    session.segment_state.on_answer_delta("同一段正文")

    assert await ctrl.on_completed_wait(
        message_id="msg_plain", answer="同一段正文", session_key="key_plain",
    ) is False
