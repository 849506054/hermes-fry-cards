"""The native bottom header controls the reasoning frame."""
from hermes_fry_cards.cardkit.builder import body_content_elements

from hermes_fry_cards.cardkit.builder import (
    PROCESS_ANCHOR_ELEMENT_ID,
    PROCESS_PANEL_ELEMENT_ID,
    build_streaming_card_v2,
)
from hermes_fry_cards.streaming.segment_helper import build_process_panel_header_action


def process_panel(card):
    return next(
        item for item in body_content_elements(card)
        if item.get("element_id") == PROCESS_PANEL_ELEMENT_ID
    )


def test_native_fold_control_uses_bottom_right_header():
    panel = process_panel(build_streaming_card_v2(show_reasoning=True, show_tool_use=False))
    assert panel["expanded"] is True
    assert panel["elements"][-1]["element_id"] == PROCESS_ANCHOR_ELEMENT_ID
    assert panel["header"]["position"] == "bottom"
    assert panel["header"]["icon_position"] == "right"
    assert panel["header"]["icon"]["token"] == "down-small-ccm_outlined"
    assert all(e["tag"] != "button" for e in panel["elements"])


def test_control_only_exists_in_reasoning_frame():
    card = build_streaming_card_v2(show_reasoning=False, show_tool_use=True)
    assert "hermes_fry_panel_action" not in str(card)


def test_header_progress_update_leaves_content_and_expansion_untouched():
    partial = build_process_panel_header_action(12)["params"]["partial_element"]
    assert set(partial) == {"header"}
    assert partial["header"]["icon_position"] == "right"
