"""拆段后的工具段必须继续用共享面板元素 id（回归 300313 抖动）。

基线（v0.5.2）行为：``split_tool_segment`` 给拆出的新段分配 ``tools_{c}``，
而 ``_new_tool`` 早已统一成共享面板 ``tool_panel``；dirty 工具段分支
``shared_el_id = tool_panel_element_id or seg.el_id`` 在面板已由骨架预置、且更早
的 flush 已走过「首次创建」分支时回落到 ``tools_N`` —— 卡片上没有这个元素，
批更新报 300313 not find elementID → 回滚重建 → 下一轮再回落，抖动不止。

修法后：拆段沿用 ``tool_panel``；dirty 分支兜底也用 ``TOOL_PANEL_ELEMENT_ID``。
"""
from __future__ import annotations

import asyncio

import pytest
from test_controller import _enable, _mock_client

from hermes_fry_cards.streaming.session import CardSession, SessionState


def _setup_ctrl():
    from hermes_fry_cards.controller import StreamCardController

    ctrl = StreamCardController()
    _enable(ctrl)
    ctrl._initialized = True
    ctrl._client = _mock_client()
    return ctrl


@pytest.mark.asyncio
async def test_split_tool_segment_keeps_shared_panel_element_id() -> None:
    ctrl = _setup_ctrl()
    client = ctrl._client

    session = CardSession("msg_split_panel", "chat_x", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.card_id = "card_split"
    session.card_msg_id = "msg_split_card"
    # 关键前置：面板由建卡骨架预置，且已在更早的 flush 里更新过。
    session.tool_panel_created = True

    session.tool_use.record_start("read", "file0")
    session.segment_state.on_tool_event(1)
    session.tool_use.record_start("read", "file1")
    session.segment_state.on_tool_event(2)

    split_seg = session.segment_state.split_tool_segment(0, 1)
    first_seg = session.segment_state.segments[0]
    first_seg.created = True
    first_seg.dirty = False
    split_seg.created = True
    split_seg.dirty = True
    session.element_count = 1
    ctrl._sessions["msg_split_panel"] = session

    await ctrl._do_flush(session)

    assert split_seg.el_id == "tool_panel", (
        "拆段必须沿用共享面板元素 id，而不是另起 tools_N"
    )
    actions = client.cardkit_batch_update.call_args_list[-1][0][1]
    ids = [
        a.get("params", {}).get("element_id")
        for a in actions
        if isinstance(a, dict)
    ]
    assert "tool_panel" in ids, f"批更新必须落在共享面板上，实际 {ids}"
    assert not [i for i in ids if str(i).startswith("tools_")], (
        f"批更新不得指向卡片上不存在的 tools_N，实际 {ids}"
    )
