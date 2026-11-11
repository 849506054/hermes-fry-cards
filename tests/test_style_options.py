"""Shared style overrides: validation, schema and per-card configuration reads."""

from __future__ import annotations

import json
import os

import pytest
import yaml

from hermes_fry_cards.config import Config
from hermes_fry_cards.style_options import STYLE_FIELDS, style_schema, validate_style_role

# One independently specified payload per role guards the public field contract.
ROLE_SAMPLES = {
    "header": {
        "template": "blue", "padding": "12px 0", "icon_token": "robot_outlined",
        "icon_color": "blue-350", "icon_img_key": "img_v3_fixture",
        "icon_processing": "img_v3_processing", "icon_completed": "img_v3_completed",
        "icon_stopped": "img_v3_stopped", "icon_error": "img_v3_error", "icon_cron": "img_v3_cron",
    },
    "body": {
        "text_size": "heading-2", "text_color": "default", "text_align": "right", "bold": True,
        "italic": False, "margin": "-99px 0 99px 2px", "light_color": "#aabbcc", "dark_color": "#001122",
        "deep_light_color": "#99aabb", "deep_dark_color": "#000f11",
        "body_padding": "99px 0", "horizontal_spacing": "extra_large", "vertical_spacing": "0px",
        "horizontal_align": "center", "vertical_align": "bottom", "surface_padding": "4px", "surface_margin": "0",
    },
    "footer": {
        "text_size": "notation", "text_color": "grey", "text_align": "center", "bold": False,
        "italic": True, "margin": "0px", "error_color": "red", "separator": " · ",
    },
    "process": {
        "padding": "4px", "margin": "-4px", "vertical_spacing": "small", "horizontal_spacing": "99px",
        "horizontal_align": "left", "vertical_align": "center", "background_color": "cus-0",
        "border_color": "rgba(255, 0, 2, 0.25)", "border_radius": "5.5px", "completed_border": False,
        "completed_color": "green", "error_color": "red", "stopped_color": "orange",
        "header_padding": "0px", "header_background_color": "grey-50", "header_vertical_align": "top",
        "header_width": "auto_when_fold", "icon_token": "down-small_ccm_outlined", "icon_color": "default",
        "icon_img_key": "img_v2_fixture", "icon_size": "1px 999px",
        "icon_position": "right", "icon_expanded_angle": -90, "arrow": True, "completed_arrow": False,
    },
    "process_title": {"text_size": "normal_v2", "text_color": "blue"},
    "reasoning": {
        "text_size": "small", "text_color": "grey-650", "text_align": "left", "bold": True,
        "italic": True, "margin": "1px 2px 3px 4px",
    },
    "reasoning_title": {
        "text_size": "heading-4", "text_color": "white", "text_align": "right", "bold": False,
        "italic": False, "margin": "1px 2px",
    },
    "summary": {
        "text_size": "xxxx-large", "text_color": "grey-1000", "text_align": "center", "bold": True,
        "italic": False, "margin": "4px",
    },
    "tool_title": {
        "text_size": "large", "text_color": "grey-00", "text_align": "left", "bold": False,
        "italic": True, "margin": "0px", "icon_color": "blue", "running_color": "yellow",
        "success_color": "green", "error_color": "red", "unknown_color": "grey",
    },
    "tool_detail": {"text_size": "normal", "text_color": "bg-white", "text_align": "left", "margin": "1px"},
    "tool_output": {
        "text_size": "heading-0", "text_color": "violet", "text_align": "left", "bold": True,
        "italic": False, "margin": "0px",
    },
    "tool_icons": {"mcp__server__fetch": "search_outlined", "namespace:tool.name-1": "done_filled"},
    "text_sizes": {"sample": {"default": "normal", "pc": "heading", "mobile": "small"}},
    "table": {
        "page_size": 10, "row_height": "auto", "row_max_height": "999px", "column_width": "100%",
        "horizontal_align": "right", "vertical_align": "bottom", "freeze_first_column": True, "margin": "-1px 0",
        "header_text_align": "center", "header_text_size": "heading", "header_background_style": "grey",
        "header_text_color": "default", "header_bold": False, "header_lines": 25,
    },
    "loading": {"img_key": "img_v3_fixture-key", "size": "16px 16px"},
    "markdown": {"heading_mode": "preserve", "max_blank_lines": 4},
    "clarify": {
        "pending_template": "default", "completed_template": "green", "hint_color": "grey",
        "active_hint_color": "blue", "primary_button_type": "danger", "secondary_button_type": "default",
    },
}


def _write(home, streaming):
    path = home / "config.yaml"
    previous = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(yaml.safe_dump({"streaming": streaming}), encoding="utf-8")
    stamp = max(path.stat().st_mtime_ns, previous + 1)
    os.utime(path, ns=(stamp, stamp))


def test_same_instance_styles_reload_and_card_snapshot_freezes(tmp_path):
    cfg = Config(tmp_path)
    _write(tmp_path, {"body": {"text_align": "left", "bold": False}})
    assert cfg.style_options("body") == {"text_align": "left", "bold": False}
    snapshot = cfg.card_snapshot()
    _write(tmp_path, {"body": {"text_align": "right", "bold": True}})
    assert cfg.style_options("body") == {"text_align": "right", "bold": True}
    assert snapshot.style_options("body") == {"text_align": "left", "bold": False}
    assert cfg.card_snapshot().style_options("body") == cfg.style_options("body")


def test_registry_rejects_unknown_and_illegal_values():
    for values in ({"tag": "script"}, {"text_color": "red'><script>"}, {"bold": "false"}):
        with pytest.raises(ValueError):
            validate_style_role("body", values)
        assert validate_style_role("body", {**values, "margin": "0px"}, strict=False) == {"margin": "0px"}


def test_schema_reports_optional_overrides():
    schema = style_schema()
    assert set(schema) == set(STYLE_FIELDS)
    for role, fields in STYLE_FIELDS.items():
        assert schema[role]["default"] == {}
        assert set(schema[role]["fields"]) == set(fields)
        assert validate_style_role(role, {}) == {}
        for field, kind in fields.items():
            assert schema[role]["fields"][field]["kind"] == kind
            assert schema[role]["fields"][field]["default"] is None
    assert json.loads(json.dumps(schema)) == schema
    schema["body"]["fields"]["text_size"]["choices"].clear()
    assert style_schema()["body"]["fields"]["text_size"]["choices"]


@pytest.mark.parametrize("role", ROLE_SAMPLES)
def test_every_registered_field_accepts_real_file_override(role, tmp_path):
    sample = ROLE_SAMPLES[role]
    assert set(ROLE_SAMPLES) == set(STYLE_FIELDS)
    if role in {"tool_icons", "text_sizes"}:
        assert "*" in STYLE_FIELDS[role]
    else:
        assert set(sample) == set(STYLE_FIELDS[role])
    assert validate_style_role(role, sample) == sample
    cfg = Config(tmp_path)
    _write(tmp_path, {role: sample})
    assert cfg.style_options(role) == sample
    snapshot = cfg.card_snapshot()
    # Empty role sections explicitly restore each renderer's defaults for new cards.
    _write(tmp_path, {role: {}})
    assert cfg.style_options(role) == {}
    assert snapshot.style_options(role) == sample
    assert cfg.card_snapshot().style_options(role) == {}
    result = snapshot.style_options(role)
    result.clear()
    assert snapshot.style_options(role) == sample


@pytest.mark.parametrize("role", ROLE_SAMPLES)
@pytest.mark.parametrize("bad", [None, [], "bad", 42, False])
def test_malformed_sections_are_isolated(role, bad, tmp_path):
    with pytest.raises(ValueError):
        validate_style_role(role, bad)
    assert validate_style_role(role, bad, strict=False) == {}
    cfg = Config(tmp_path)
    _write(tmp_path, {role: bad})
    assert cfg.style_options(role) == {}


@pytest.mark.parametrize("role", ["missing", "", None, [], 5])
def test_unknown_roles_are_safe(role, tmp_path):
    with pytest.raises(ValueError):
        validate_style_role(role, {})
    assert validate_style_role(role, {}, strict=False) == {}
    assert Config(tmp_path).style_options(role) == {}


@pytest.mark.parametrize(("role", "field"), [
    ("header", "text_size"), ("header", "bold"), ("header", "text_color"),
    ("process", "border_width"), ("process", "header_text_size"), ("process", "expanded"),
    ("process_title", "bold"), ("body", "direction"), ("body", "width"), ("table", "text_size"),
    ("tool_detail", "bold"), ("table", "rows"), ("clarify", "callback"),
])
def test_component_and_ownership_boundaries(role, field):
    with pytest.raises(ValueError):
        validate_style_role(role, {field: "normal"})
    assert validate_style_role(role, {field: "normal"}, strict=False) == {}


@pytest.mark.parametrize(("role", "field", "good", "bad"), [
    ("body", "body_padding", ["0", "0px", "99px", "1px 2px", "1px 2px 3px 4px"],
     ["-1px", "100px", "1px 2px 3px", "1px\n2px", "1", 0, " 0px", "0px ", "0px  0px"]),
    ("body", "margin", ["-99px", "99px", "-1px 0", "-1px 0 1px 0"], ["-100px", "100px", "1em", "0; color:red"]),
    ("body", "vertical_spacing", ["small", "medium", "large", "extra_large", "0px", "99px"],
     ["100px", "-1px", "0", "giant"]),
    ("process", "icon_size", ["1px 1px", "999px 999px"], ["0px 2px", "1000px 2px", "16px", "16px 16px 16px"]),
    ("process", "border_radius", ["0px", "5px", "5.5px"], ["-1px", "5%", "5px 2px", "calc(1px)", "nanpx"]),
    ("process", "icon_expanded_angle", [-180, -90, 90, 180], [0, 45, 360, "90", 90.0, True]),
    ("process", "header_width", ["fill", "auto", "auto_when_fold"], ["100px", "50%", "weighted"]),
    ("table", "page_size", [1, 10], [0, 11, 1.0, "2", True, float("nan")]),
    ("table", "row_height", ["low", "middle", "high", "auto", "32px", "124px"], ["31px", "125px", "32", 32]),
    ("table", "row_max_height", ["32px", "999px"], ["31px", "1000px", "auto"]),
    ("table", "column_width", ["auto", "80px", "600px", "1%", "100%"], ["79px", "601px", "0%", "101%", "1.5%"]),
    ("table", "header_lines", [1, 100], [0, -1, 1.5, "1", True]),
    ("table", "header_text_size", ["normal", "heading"], ["notation", "heading-1"]),
    ("table", "header_text_color", ["default", "grey"], ["red", "cus-0", "rgba(0,0,0,1)"]),
    ("table", "header_background_style", ["none", "grey"], ["default", "red"]),
    ("markdown", "max_blank_lines", [1, 4], [0, 5, "1", 1.0, True]),
    ("markdown", "heading_mode", ["compact", "preserve"], ["strip", "COMPACT"]),
    ("body", "text_size", ["heading-0", "heading-1", "heading-2", "heading-3", "heading-4", "heading", "normal",
                            "normal_v2", "notation", "xxxx-large", "xxx-large", "xx-large", "x-large", "large",
                            "medium", "small", "x-small"], ["heading-5", "huge", "14px", "<b>"]),
    ("body", "text_color", ["default", "cus-0", "blue-350", "grey-00", "grey-950", "grey-1000",
                             "rgba(0, 255, 99, 0)", "rgba(0,0,0,1)", "rgba(255,255,255,0.99)"],
     ["cus-1", "unknown", "blue-650", "blue-999", "grey-0", "#ffffff", "rgba(256,0,0,1)", "rgba(0,0,0,1.01)",
      "rgba(-1,0,0,0)", "rgba(0,0,0,nan)", "red'", 'red"', "red><script>", "blue\n", "rgb(0,0,0)"]),
    ("body", "light_color", ["#abcdef", "#012345"], ["#fff", "red", "rgba(0,0,0,1)", "#abcdef\n"]),
    ("body", "bold", [True, False], [0, 1, "true", "false", None, [], {}]),
    ("header", "icon_token", ["robot_outlined", "done_filled", "chat-forbidden_outlined", "vc-video_colorful"],
     ["robot", "robot_outlined'", "<img>", "robot_outlined\n", "https://icon", "a" * 200]),
    ("loading", "img_key", ["img_v2_fixture", "img_v3_0000_fixture-key"],
     ["https://example.org/img.png", "data:image/png;base64,x", "img_x'", "img_<x>", "img_", "a" * 300]),
    ("footer", "separator", ["", " · ", " | ", " / ", "—"], ["<br>", "&#60;", "\n", "x" * 65]),
    ("clarify", "primary_button_type", ["default", "primary", "danger"], ["warning", "<button>"]),
])
def test_kind_boundaries_and_payload_safety(role, field, good, bad):
    for value in good:
        assert validate_style_role(role, {field: value}) == {field: value}
    for value in bad:
        with pytest.raises(ValueError):
            validate_style_role(role, {field: value})
        assert validate_style_role(role, {field: value}, strict=False) == {}


@pytest.mark.parametrize("name", [
    "__proto__", "constructor", "prototype", "<tool>", "a b", "a/b", "", "x\n", 1, "x" * 129,
])
def test_tool_icon_keys_are_safe(name):
    with pytest.raises(ValueError):
        validate_style_role("tool_icons", {name: "search_outlined"})
    assert validate_style_role("tool_icons", {name: "search_outlined", "safe_tool": "done_filled"}, strict=False) == {
        "safe_tool": "done_filled",
    }


@pytest.mark.parametrize("role", ROLE_SAMPLES)
def test_every_registered_field_rejects_structured_payloads(role):
    for field in ROLE_SAMPLES[role]:
        for value in (None, [], {"tag": "script"}):
            with pytest.raises(ValueError):
                validate_style_role(role, {field: value})
            assert validate_style_role(role, {field: value}, strict=False) == {}


def test_schema_choices_are_valid_and_runtime_defaults_stay_empty():
    for role, metadata in style_schema().items():
        assert validate_style_role(role, {}) == {}
        for field, spec in metadata["fields"].items():
            actual_field = "safe_tool" if field == "*" else field
            for value in spec.get("choices", []):
                assert validate_style_role(role, {actual_field: value}) == {actual_field: value}


def test_errors_keep_values_and_unknown_fields_private():
    marker = "private-payload-marker"
    for role, values in [("body", {"text_color": marker}), ("body", {marker: True}), ("tool_icons", {marker: marker})]:
        with pytest.raises(ValueError) as error:
            validate_style_role(role, values)
        assert marker not in str(error.value)


def test_style_reads_preserve_legacy_owners_and_defaults(tmp_path):
    cfg = Config(tmp_path)
    assert all(cfg.style_options(role) == {} for role in STYLE_FIELDS)
    assert cfg.header_template == "default"
    assert cfg.body_text_size == "normal_v2"
    assert cfg.footer_text_size == "notation"
    assert cfg.panel_text_size == "notation"
    _write(tmp_path, {
        "header": {"enabled": False, "template": "blue", "padding": "4px"},
        "body": {"text_size": "heading-4", "light_color": "#abcdef", "bold": False, "text_color": "red'"},
        "footer": {"text_size": "normal", "enabled": True, "fields": [["model"]]},
    })
    assert cfg.header_enabled is False
    assert cfg.style_options("header") == {"template": "blue", "padding": "4px"}
    assert cfg.style_options("body") == {"text_size": "heading-4", "light_color": "#abcdef", "bold": False}
    assert cfg.body_text_size == "heading-4"
    assert cfg.body_light_color == "#ABCDEF"
    assert cfg.style_options("footer") == {"text_size": "normal"}
    assert cfg.footer_enabled is True
    assert cfg.footer_fields == [["model"]]


def test_invalid_yaml_keeps_styles_and_snapshot_recovers_independently(tmp_path):
    cfg = Config(tmp_path)
    path = tmp_path / "config.yaml"
    _write(tmp_path, {"body": {"bold": True}})
    snapshot = cfg.card_snapshot()
    path.write_text("streaming: [", encoding="utf-8")
    assert cfg.style_options("body") == {"bold": True}
    _write(tmp_path, {"body": {"bold": False, "margin": "garbage"}})
    assert cfg.style_options("body") == {"bold": False}
    assert snapshot.style_options("body") == {"bold": True}
