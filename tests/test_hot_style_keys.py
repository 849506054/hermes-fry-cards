"""Presentation knobs resolve from config on every build, so edits apply without a restart."""
from hermes_fry_cards.cardkit.builder import (
    PROCESS_PANEL_ELEMENT_ID,
    _body_style,
    body_content_elements,
    build_complete_card,
)
from hermes_fry_cards.config import Config
from hermes_fry_cards.streaming.segments import SegmentState


def build(body=None, process=None):
    cfg = Config()
    cfg._streaming_snapshot = {"enabled": True, "body": body or {}, "process": process or {}}
    card = build_complete_card(
        config=cfg, segments=SegmentState().segments, all_tool_steps=[],
        process_messages=["progress"], footer_data={"duration": 3},
    )
    panel = next(e for e in body_content_elements(card)
                 if e.get("element_id") == PROCESS_PANEL_ELEMENT_ID)
    return cfg, panel


def test_deep_shade_defaults_one_step_below_the_body():
    cfg, _ = build()
    deep = _body_style(config=cfg)["color"]["cus-1"]
    assert deep["light_mode"] == "rgba(230,231,232,1)"
    assert deep["dark_mode"] == "rgba(26,26,26,1)"


def test_deep_shade_follows_an_explicit_body_colour():
    cfg = Config()
    cfg._streaming_snapshot = {"enabled": True, "body": {
        "light_color": "#ffffff", "dark_color": "#202020",
        "deep_light_color": "#f0f0f0", "deep_dark_color": "#101010",
    }}
    deep = _body_style(config=cfg)["color"]["cus-1"]
    assert deep == {"light_mode": "rgba(240,240,240,1)", "dark_mode": "rgba(16,16,16,1)"}


def test_panel_background_and_radius_are_configurable():
    _, panel = build(process={"background_color": "cus-0", "border_radius": "10px"})
    assert panel["background_color"] == "cus-0"
    assert panel["border"]["corner_radius"] == "10px"


def test_panel_defaults_keep_the_deep_shade():
    _, panel = build()
    assert panel["background_color"] == "cus-1"
    assert panel["border"] == {"color": "cus-1", "corner_radius": "5px"}
