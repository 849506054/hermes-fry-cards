"""Reply bodies use the native theme surface throughout their lifecycle."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from hermes_fry_cards.cardkit import builder
from hermes_fry_cards.streaming.controller import StreamingController
from hermes_fry_cards.streaming.segments import SegmentState


@pytest.mark.parametrize("status", ["streaming", "completed", "error", "stopped", "overflow"])
@pytest.mark.parametrize("show_tools", [False, True])
def test_reply_body_uses_theme_surface_and_preserves_children(status, show_tools, monkeypatch):
    state = SegmentState()
    state._new_reasoning("Reasoning fixture")
    state.on_answer_delta("Answer fixture")

    def render():
        if status == "streaming":
            return builder.build_streaming_card_v2(
                show_reasoning=True, show_tool_use=show_tools, header_enabled=True,
                notice="Notice fixture",
            )
        if status == "overflow":
            return builder.build_overflow_fallback_card()
        return builder.build_complete_card(
            segments=state.segments, all_tool_steps=[], header_enabled=True,
            show_reasoning=True, show_tool_use=show_tools, notices=["Notice fixture"],
            reasoning_count=1, tool_count=1,
            is_error=status == "error", is_aborted=status == "stopped",
        )

    card = render()
    root = card["body"]["elements"]
    if status == "streaming":
        assert [element["element_id"] for element in root] == [
            builder.BODY_SURFACE_ELEMENT_ID, builder.LOADING_CURSOR_ELEMENT_ID,
        ]
    else:
        assert len(root) == 1
    surface = root[0]
    assert surface["tag"] == "column_set"
    assert surface["background_style"] == "cus-0"
    column, = surface["columns"]
    assert column["tag"] == "column"
    assert "background_style" not in column
    assert column["width"] == "weighted" and column["weight"] == 1
    content = builder.body_content_elements(card)
    assert content[:len(column["elements"])] == column["elements"]
    if status != "streaming":
        assert content is column["elements"]
    assert "template" not in card["header"]
    assert card["config"]["style"]["color"]["cus-0"] == {
        "light_mode": "rgba(245,246,247,1)", "dark_mode": "rgba(34,34,34,1)",
    }
    panels = [node for node in builder.body_content_elements(card)
              if node.get("tag") == "collapsible_panel"]
    if status == "streaming":
        assert panels and all("border" not in panel for panel in panels)
        assert all(panel["header"]["icon"]["token"] == "down-small-ccm_outlined"
                   for panel in panels)
    elif status == "stopped":
        # 停止卡不建过程框（用户口径）：停止后由新卡接手。
        assert not panels
    elif status != "overflow":
        panel = next(node for node in panels if node.get("element_id") == "unified_panel")
        expected = "red" if status == "error" else builder.BODY_SURFACE_DEEP_COLOR
        assert panel["border"] == {"color": expected, "corner_radius": "5px"}

    unwrapped = deepcopy(card)
    unwrapped["body"]["elements"] = builder.body_content_elements(unwrapped)
    monkeypatch.setattr(builder, "_build_body", lambda elements: {"elements": elements})
    assert unwrapped == render()
    if status == "streaming":
        assert card["config"]["streaming_mode"] is True
        assert builder.body_content_elements(card)[-1]["element_id"] == builder.LOADING_CURSOR_ELEMENT_ID
        assert card["body"]["elements"][-1]["element_id"] == builder.LOADING_CURSOR_ELEMENT_ID


@pytest.mark.parametrize("kind", ["cron", "completed"])
def test_native_table_stays_at_body_root_with_themed_text(kind):
    text = "Before\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nAfter"
    if kind == "cron":
        card = builder.build_cron_card(text, task_name="fixture")
    else:
        state = SegmentState()
        state._new_reasoning("reason")
        state.on_answer_delta(text)
        card = builder.build_complete_card(
            segments=state.segments, all_tool_steps=[], show_reasoning=True,
            reasoning_count=1, footer_data={"duration": 30},
        )
    root = card["body"]["elements"]
    assert any(el.get("tag") == "table" for el in root)
    surfaces = [el for el in root if el.get("tag") == "column_set"]
    assert surfaces
    assert len({el["element_id"] for el in surfaces}) == len(surfaces)
    assert all(len(el["element_id"]) <= 20 for el in surfaces)
    for surface in surfaces:
        assert surface["background_style"] == "cus-0"
        assert all(el.get("tag") != "table" for el in surface["columns"][0]["elements"])
    flat = builder.body_content_elements(card)
    assert [el["tag"] for el in flat].count("table") == 1
    if kind == "completed":
        assert builder.strip_unified_panel(card) == 1
        assert all(el.get("element_id") != "unified_panel" for el in builder.body_content_elements(card))
        assert any(el.get("tag") == "table" for el in card["body"]["elements"])


@pytest.mark.parametrize("timeline,base", [(False, 1), (True, 5)])
def test_theme_surface_is_counted_in_streaming_budget(timeline, base):
    session = SimpleNamespace(reasoning_timeline=timeline)
    assert StreamingController._skeleton_element_count(None, session) == base + 3


@pytest.mark.parametrize("kind", ["reasoning", "tool"])
def test_appended_stream_panel_preserves_header_and_omits_border(kind):
    from hermes_fry_cards.streaming.segment_helper import build_add_segment_action

    state = SegmentState()
    if kind == "reasoning":
        segment = state._new_reasoning("Reasoning fixture")
    else:
        state.on_tool_event(1)
        segment = state.segments[-1]
    action = build_add_segment_action(segment, [])
    panel, = action["params"]["elements"]
    assert panel["tag"] == "collapsible_panel"
    assert "border" not in panel
    assert panel["header"]["icon"]["token"] == "down-small-ccm_outlined"
    assert panel["header"]["position"] == "bottom"
    assert panel["element_id"] == segment.el_id

