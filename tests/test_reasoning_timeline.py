"""思考时间线：只隐藏工具的展示，保留阶段边界和正文。"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from hermes_fry_cards.cardkit.builder import (
    PROCESS_PANEL_ELEMENT_ID,
    build_complete_card,
    build_streaming_card_v2,
)
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.feishu import FeishuClient
from hermes_fry_cards.streaming.segment_helper import (
    build_add_segment_action,
    build_reasoning_finalized_action,
)
from hermes_fry_cards.streaming.segments import SegmentState
from hermes_fry_cards.streaming.session import CardSession


def nodes(obj):
    if isinstance(obj, dict):
        if "tag" in obj:
            yield obj
        for value in obj.values():
            yield from nodes(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from nodes(value)


def stages(count=4):
    state = SegmentState(max_reasoning_panels=count)
    for i in range(count):
        state.on_reasoning_delta(f"验证用阶段 {i + 1}")
        state.on_tool_event(i + 1)
    state.on_answer_delta("验证用最终正文")
    state.finalize_segments(count)
    return state


def test_streaming_outer_is_expanded_and_has_no_tool_panel():
    card = build_streaming_card_v2(show_reasoning=True, show_tool_use=False)
    panel = next(n for n in nodes(card) if n.get("element_id") == PROCESS_PANEL_ELEMENT_ID)
    assert panel["expanded"] is True
    assert not any(n.get("element_id", "").startswith("tool_panel") for n in nodes(card))


def test_stage_completion_leaves_user_expansion_state_unchanged():
    seg = stages(1).segments[0]
    partial = build_reasoning_finalized_action(seg, reasoning_timeline=True)["params"]["partial_element"]
    assert "expanded" not in partial
    assert "header" not in partial
    assert "content" in partial


def test_answer_sits_below_the_process_frame():
    seg = stages(1).segments[-1]
    action = build_add_segment_action(seg, [], reasoning_timeline=True)
    assert action["params"]["target_element_id"] == "loading_icon"


@pytest.mark.parametrize("duration", [0.1, 10])
def test_reasoning_survives_hidden_tools_and_keeps_chronological_order(duration):
    state = stages()
    card = build_complete_card(
        segments=state.segments, all_tool_steps=[], show_tool_use=False,
        footer_data={"duration": duration}, footer_enabled=False,
    )
    outer = next(n for n in nodes(card) if n.get("element_id") == PROCESS_PANEL_ELEMENT_ID)
    assert outer["expanded"] is False
    # 封卡后只留一行轮数摘要，逐步标题、耗时与正文都不进卡
    assert len(outer["elements"]) == 1
    assert outer["elements"][0]["tag"] == "markdown"
    assert "思考了 4 轮" in outer["elements"][0]["content"]
    assert "验证用阶段" not in json.dumps(outer, ensure_ascii=False)
    assert not any(n.get("element_id", "").startswith("tool_panel") for n in nodes(card))
    assert "验证用最终正文" in json.dumps(card, ensure_ascii=False)


def test_streaming_stage_is_two_siblings_under_one_outer():
    seg = stages(1).segments[0]
    action = build_add_segment_action(seg, [], reasoning_timeline=True)
    items = action["params"]["elements"]
    assert len(items) == 2
    assert [n["tag"] for n in items] == ["markdown", "markdown"]
    assert items[0]["element_id"] == seg.el_id
    assert items[1]["element_id"] == seg.text_el_id
    card = build_streaming_card_v2(show_reasoning=True, show_tool_use=False)
    outer = next(n for n in nodes(card) if n.get("element_id") == PROCESS_PANEL_ELEMENT_ID)
    outer["elements"][:0] = items
    assert sum(n.get("tag") == "collapsible_panel" for n in nodes(card)) == 1


def test_sealed_summary_counts_all_rounds(tmp_path):
    state = stages(57)
    for seg in state.segments:
        if seg.type == "reasoning":
            seg.elapsed_ms = 1000.0
    card = build_complete_card(
        segments=state.segments, all_tool_steps=[], show_tool_use=False,
        footer_data={"duration": 10.0}, footer_enabled=False,
    )
    outer = next(n for n in nodes(card) if n.get("element_id") == PROCESS_PANEL_ELEMENT_ID)
    assert len(outer["elements"]) == 1
    assert "思考了 57 轮" in outer["elements"][0]["content"]


def test_many_rounds_keep_distinct_timeline_segments():
    state = SegmentState(max_reasoning_panels=None)
    for i in range(55):
        state.on_reasoning_delta(f"验证阶段 {i}")
        state.on_tool_event(i + 1)
    assert len([s for s in state.segments if s.type == "reasoning"]) == 55


def test_timeline_byte_estimate_bounds_long_rounds(tmp_path):
    ctrl = StreamCardController(tmp_path)
    state = stages(15)
    for seg in state.segments:
        if seg.type == "reasoning":
            seg.text = "测试" * 10000
    assert ctrl._timeline_byte_estimate(state.segments) > 160_000
    assert ctrl._timeline_byte_estimate(state.segments[:20]) < 160_000


@pytest.mark.asyncio
async def test_recreated_card_hides_tool_skeleton_and_retains_reasoning_frame(tmp_path):
    ctrl = StreamCardController(tmp_path)
    cfg = {"streaming": {"enabled": True}, "display": {
        "platforms": {"feishu": {"show_reasoning": True, "show_tool_use": False}},
    }}
    ctrl._cfg._raw = cfg
    ctrl._cfg._reload = lambda: cfg
    ctrl._client = AsyncMock(spec=FeishuClient)
    ctrl._client.cardkit_create.return_value = "fixture_card"
    ctrl._client.reply_card_by_id.return_value = "fixture_reply"
    session = CardSession("fixture_msg", "fixture_chat", asyncio.get_running_loop())
    session.segment_state = stages(1)
    assert await ctrl._create_streaming_card(session)
    card = ctrl._client.cardkit_create.call_args.args[0]
    assert not any(n.get("element_id", "").startswith("tool_panel") for n in nodes(card))
    assert any(n.get("element_id") == PROCESS_PANEL_ELEMENT_ID for n in nodes(card))
