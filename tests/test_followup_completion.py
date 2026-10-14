"""#35 — 排队续轮那一轮自己的卡要能封住，并抑制重复的纯文本投递.

问题：续轮由 ``_run_agent_queued_followup`` 递归 ``_run_agent`` 执行，不经过
``_handle_message_with_agent``，所以按轮次的 COMPLETE 钩子对该轮不触发——卡片停在
「处理中」，回复又按 Hermes 默认路径发成纯文本。

新增的 ``on_queued_followup_completed`` 在 ``_preserve_queued_followup_history_offset``
之前注入，负责封该轮的卡并把 ``already_sent`` / ``final_response`` 写回 result，让外层
合并后走「已投递」分支。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import hermes_fry_cards.patch as patch_module
from hermes_fry_cards.patch import on_queued_followup_completed


def _ctrl(sent: bool = True) -> MagicMock:
    ctrl = MagicMock()
    ctrl.enabled = True
    ctrl.sealed_message_id = None

    async def _completed(**kwargs):
        ctrl.sealed_message_id = kwargs.get("message_id")
        return sent

    ctrl.on_completed_wait = _completed
    ctrl.consume_text_fallback = MagicMock(return_value=False)
    return ctrl


@pytest.mark.asyncio
async def test_seals_card_and_suppresses_plain_text() -> None:
    ctrl = _ctrl(sent=True)
    result = {"final_response": "续轮的答复", "model": "m", "input_tokens": 3, "output_tokens": 4}
    with patch.object(patch_module, "get_controller", return_value=ctrl):
        assert await on_queued_followup_completed(
            message_id="syn-abc", result=result, session_key="k",
        ) is True

    # 封的是续轮自己那张卡
    assert ctrl.sealed_message_id == "syn-abc"
    assert ctrl.consume_text_fallback.call_count == 0
    # 外层据此判定本轮已投递：不再发纯文本，且 final_response 被清空
    assert result["already_sent"] is True
    assert result["response_previewed"] is True
    assert result["final_response"] == ""


@pytest.mark.asyncio
async def test_interrupted_turn_is_left_untouched() -> None:
    """续轮被中断时不该封卡——内容本就该丢弃。"""
    ctrl = _ctrl()
    result = {"final_response": "半截", "interrupted": True}
    with patch.object(patch_module, "get_controller", return_value=ctrl):
        assert await on_queued_followup_completed(
            message_id="syn-abc", result=result, session_key="k",
        ) is False

    assert "already_sent" not in result
    assert result["final_response"] == "半截", "中断轮不得被改写"


@pytest.mark.asyncio
async def test_failed_seal_falls_back_to_plain_text() -> None:
    """卡片没封成时不能吞掉投递——否则这轮彻底静默。"""
    ctrl = _ctrl(sent=False)
    result = {"final_response": "续轮的答复"}
    with patch.object(patch_module, "get_controller", return_value=ctrl):
        assert await on_queued_followup_completed(
            message_id="syn-abc", result=result, session_key="k",
        ) is False

    assert "already_sent" not in result, "封卡失败时必须放行纯文本"
    assert result["final_response"] == "续轮的答复"
    ctrl.consume_text_fallback.assert_called_once_with("syn-abc")


@pytest.mark.asyncio
async def test_non_dict_result_is_ignored() -> None:
    ctrl = _ctrl()
    with patch.object(patch_module, "get_controller", return_value=ctrl):
        assert await on_queued_followup_completed(message_id="syn-abc", result=None) is False
