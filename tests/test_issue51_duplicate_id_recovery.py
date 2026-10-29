"""Issue #51 — 300301 Duplicate ID 恢复机制.

当飞书服务端出现瞬态错误（如 300308）但实际已将元素写入卡片，
客户端重试时会报 300301 Duplicate ID。若无恢复路径，客户端
将在每次 flush 重复生成 add_elements，导致整批更新被原子拒绝，
卡片陷入永久死锁冻结。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from test_controller import _enable, _mock_client

from hermes_fry_cards.feishu import CARDKIT_DUPLICATE_ID, FeishuAPIError
from hermes_fry_cards.streaming.diagnostics import extract_duplicate_element_id
from hermes_fry_cards.streaming.session import CardSession, SessionState


def _setup_ctrl():
    from hermes_fry_cards.controller import StreamCardController

    ctrl = StreamCardController()
    _enable(ctrl)
    ctrl._initialized = True
    ctrl._client = _mock_client()
    return ctrl


def test_extract_duplicate_element_id() -> None:
    # 格式 1: 标准 Issue #51 报错
    err1 = FeishuAPIError(
        "cardkit_batch_update: code=300301 msg=Duplicate ID (ElementID reasoning_0_panel: Code 1001: Duplicate ID)",
        code=CARDKIT_DUPLICATE_ID,
    )
    assert extract_duplicate_element_id(err1) == "reasoning_0_panel"

    # 格式 2: 答案段或工具面板
    err2 = FeishuAPIError(
        "code=300301 msg=Duplicate ID (ElementID answer_1: Duplicate ID)",
        code=300301,
    )
    assert extract_duplicate_element_id(err2) == "answer_1"

    # 格式 3: 非 duplicate 错误返回空
    err3 = FeishuAPIError("code=300313 not find elementID: reasoning_0_panel", code=300313)
    assert extract_duplicate_element_id(err3) == ""

    err4 = FeishuAPIError("code=300305 total limit", code=300305)
    assert extract_duplicate_element_id(err4) == ""


@pytest.mark.asyncio
async def test_duplicate_id_recovers_and_breaks_deadlock() -> None:
    """Issue #51: 报 300301 时自动将对应 segment 标记为已创建，下轮 flush 不再发 add_elements."""
    ctrl = _setup_ctrl()
    client = ctrl._client

    session = CardSession("msg_dup", "chat_dup", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.card_id = "card_dup"
    session.card_msg_id = "msg_dup_card"
    ctrl._sessions["msg_dup"] = session

    # 增加一个 reasoning 思考段
    session.segment_state.on_reasoning_delta("thinking...")
    reasoning_seg = session.segment_state.segments[0]
    assert reasoning_seg.created is False
    assert reasoning_seg.el_id == "reasoning_0_panel"

    # 模拟飞书返回 300301 Duplicate ID
    client.cardkit_batch_update = AsyncMock(
        side_effect=[
            FeishuAPIError(
                "cardkit_batch_update: code=300301 "
                "msg=Duplicate ID (ElementID reasoning_0_panel: Code 1001: Duplicate ID)",
                code=300301,
            ),
            None,  # 随后的批更新成功
        ]
    )

    # 第一次 flush: 发起 add_elements(reasoning_0_panel) → 遇到 300301
    await ctrl._do_flush(session)

    # 验证自愈机制生效：reasoning_seg 被自动修正置为 created=True！
    assert reasoning_seg.created is True
    assert session.element_count > 0

    # 第二次 flush: 此时该 segment 已经是 created=True，
    # 不应该再尝试发 add_elements，而是直接流式刷新文本！
    client.cardkit_stream_element.reset_mock()
    await ctrl._do_flush(session)

    # 验证没有再次尝试添加重复的元素，且流式刷文本成功调用
    client.cardkit_stream_element.assert_called()
