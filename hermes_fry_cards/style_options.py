"""Shared, Config-independent presentation overrides and Studio field schema.

Fields are optional: an omitted value inherits the renderer's established output.
Schema ``default=None`` means inherit; validation returns explicit values only.
The ``tool_icons.*`` entry describes a map keyed by safe, exact tool names.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

TEXT_SIZES = (
    "heading-0", "heading-1", "heading-2", "heading-3", "heading-4", "heading",
    "normal", "notation", "xxxx-large", "xxx-large", "xx-large", "x-large",
    "large", "medium", "small", "x-small", "normal_v2",
)
PLATFORM_TEXT_SIZES = tuple(size for size in TEXT_SIZES if size != "normal_v2")
HEADER_TEMPLATES = (
    "default", "blue", "wathet", "turquoise", "green", "yellow", "orange",
    "red", "carmine", "violet", "purple", "indigo", "grey",
)
# https://open.feishu.cn/document/feishu-cards/enumerations-for-fields-related-to-color
_COLOR_FAMILIES = (
    "blue", "carmine", "green", "indigo", "lime", "grey", "orange", "purple",
    "red", "sunflower", "turquoise", "violet", "wathet", "yellow",
)
_COLOR_LEVELS = (50, 100, 200, 300, 350, 400, 500, 600, 700, 800, 900)
COLORS = (
    "default", "bg-white", "white", "cus-0", *_COLOR_FAMILIES,
    *(f"{color}-{level}" for color in _COLOR_FAMILIES for level in _COLOR_LEVELS),
    "grey-00", "grey-650", "grey-950", "grey-1000",
)

_TEXT_FIELDS = {
    "text_size": "text_size", "text_color": "color", "text_align": "align",
    "bold": "bool", "italic": "bool", "margin": "margin",
}
STYLE_FIELDS: dict[str, dict[str, str]] = {
    "header": {
        "template": "template", "padding": "padding", "icon_token": "icon_token",
        "icon_color": "color", "icon_img_key": "img_key",
        "icon_processing": "img_key", "icon_completed": "img_key",
        "icon_stopped": "img_key", "icon_error": "img_key", "icon_cron": "img_key",
    },
    "body": {
        **_TEXT_FIELDS, "light_color": "hex_color", "dark_color": "hex_color",
        "deep_light_color": "hex_color", "deep_dark_color": "hex_color",
        "body_padding": "padding", "horizontal_spacing": "spacing", "vertical_spacing": "spacing",
        "horizontal_align": "align", "vertical_align": "vertical_align",
        "surface_padding": "padding", "surface_margin": "margin",
    },
    "footer": {**_TEXT_FIELDS, "error_color": "color", "separator": "separator"},
    "process": {
        "padding": "padding", "margin": "margin", "vertical_spacing": "spacing",
        "horizontal_spacing": "spacing", "horizontal_align": "align", "vertical_align": "vertical_align",
        "background_color": "color", "border_color": "color", "border_radius": "radius",
        "completed_border": "bool", "completed_color": "color", "error_color": "color", "stopped_color": "color",
        "header_padding": "padding", "header_background_color": "color",
        "header_vertical_align": "vertical_align", "header_width": "header_width",
        "icon_token": "icon_token", "icon_color": "color", "icon_img_key": "img_key",
        "icon_size": "icon_size", "icon_position": "icon_position", "icon_expanded_angle": "angle",
        "arrow": "bool", "completed_arrow": "bool",
    },
    "process_title": {"text_size": "text_size", "text_color": "color"},
    "reasoning": dict(_TEXT_FIELDS),
    "reasoning_title": dict(_TEXT_FIELDS),
    "summary": dict(_TEXT_FIELDS),
    "tool_title": {
        **_TEXT_FIELDS, "icon_color": "color", "running_color": "color", "success_color": "color",
        "error_color": "color", "unknown_color": "color",
    },
    "tool_detail": {
        "text_size": "text_size", "text_color": "color", "text_align": "align", "margin": "margin",
    },
    "tool_output": dict(_TEXT_FIELDS),
    "tool_icons": {"*": "icon_token"},
    "text_sizes": {"*": "platform_text_size"},
    "table": {
        "page_size": "page_size", "row_height": "row_height", "row_max_height": "row_max_height",
        "column_width": "column_width", "horizontal_align": "align", "vertical_align": "vertical_align",
        "freeze_first_column": "bool", "margin": "margin", "header_text_align": "align",
        "header_text_size": "table_text_size", "header_background_style": "table_background",
        "header_text_color": "table_color", "header_bold": "bool", "header_lines": "positive_int",
    },
    "loading": {"img_key": "img_key", "size": "icon_size"},
    "markdown": {"heading_mode": "heading_mode", "max_blank_lines": "blank_lines"},
    "clarify": {
        "pending_template": "template", "completed_template": "template", "hint_color": "color",
        "active_hint_color": "color", "primary_button_type": "button_type", "secondary_button_type": "button_type",
    },
}

_TOOL_NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}"
_RESERVED_NAMES = ("__proto__", "prototype", "constructor")
_TEXT_SIZE_NAME_PATTERN = r"[A-Za-z][A-Za-z0-9_-]{0,63}"
_ICON_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}_(?:outlined|filled|colorful)"
_IMG_PATTERN = r"img_[A-Za-z0-9_-]{1,252}"
_RGBA_PATTERN = r"rgba\( *([0-9]{1,3}) *, *([0-9]{1,3}) *, *([0-9]{1,3}) *, *(0(?:\.[0-9]+)?|1(?:\.0+)?) *\)"

# Bounds mirror Card JSON 2.0 components: rich-text, table and collapsible-panel.
_KIND_SPECS: dict[str, dict[str, Any]] = {
    "text_size": {"type": "string", "choices": list(TEXT_SIZES), "allow_custom": True,
                  "references": "text_sizes"},
    "platform_text_size": {
        "type": "object", "properties": {
            platform: {"type": "string", "choices": list(PLATFORM_TEXT_SIZES)}
            for platform in ("default", "pc", "mobile")
        },
        "min_properties": 1,
    },
    "template": {"type": "string", "choices": list(HEADER_TEMPLATES)},
    "color": {"type": "string", "choices": list(COLORS), "allow_custom": True, "format": "color", "max_length": 128},
    "hex_color": {"type": "string", "pattern": r"#[0-9a-fA-F]{6}", "format": "hex", "max_length": 7},
    "align": {"type": "string", "choices": ["left", "center", "right"]},
    "vertical_align": {"type": "string", "choices": ["top", "center", "bottom"]},
    "header_width": {"type": "string", "choices": ["fill", "auto", "auto_when_fold"]},
    "icon_position": {"type": "string", "choices": ["left", "right"]},
    "angle": {"type": "integer", "choices": [-180, -90, 90, 180]},
    "bool": {"type": "boolean"},
    "padding": {"type": "string", "min": 0, "max": 99, "unit": "px", "arity": [1, 2, 4], "max_length": 32},
    "margin": {"type": "string", "min": -99, "max": 99, "unit": "px", "arity": [1, 2, 4], "max_length": 32},
    "spacing": {
        "type": "string", "choices": ["small", "medium", "large", "extra_large"],
        "allow_custom": True, "min": 0, "max": 99, "unit": "px", "max_length": 16,
    },
    "radius": {"type": "string", "min": 0, "unit": "px", "pattern": r"[0-9]+(?:\.[0-9]+)?px", "max_length": 32},
    "icon_size": {"type": "string", "min": 1, "max": 999, "unit": "px", "arity": [2], "max_length": 16},
    "icon_token": {"type": "string", "pattern": _ICON_PATTERN, "max_length": 128},
    "img_key": {"type": "string", "pattern": _IMG_PATTERN, "max_length": 256},
    "separator": {"type": "string", "max_length": 64},
    "page_size": {"type": "integer", "min": 1, "max": 10},
    "row_height": {
        "type": "string", "choices": ["low", "middle", "high", "auto"],
        "allow_custom": True, "min": 32, "max": 124, "unit": "px", "max_length": 16,
    },
    "row_max_height": {"type": "string", "min": 32, "max": 999, "unit": "px", "max_length": 16},
    "column_width": {
        "type": "string", "choices": ["auto"], "allow_custom": True,
        "min": 80, "max": 600, "unit": "px", "percent_min": 1, "percent_max": 100, "max_length": 16,
    },
    "table_text_size": {"type": "string", "choices": ["normal", "heading"]},
    "table_background": {"type": "string", "choices": ["none", "grey"]},
    "table_color": {"type": "string", "choices": ["default", "grey"]},
    "positive_int": {"type": "integer", "min": 1},
    "heading_mode": {"type": "string", "choices": ["compact", "preserve"]},
    "blank_lines": {"type": "integer", "min": 1, "max": 4},
    "button_type": {"type": "string", "choices": ["default", "primary", "danger"]},
}


def style_schema() -> dict[str, dict[str, Any]]:
    """Return independent, JSON-serializable role metadata for optional overrides."""
    schema = {}
    for role, fields in STYLE_FIELDS.items():
        schema[role] = {
            "default": {},
            "fields": {
                field: {"kind": kind, "default": None, **deepcopy(_KIND_SPECS[kind])}
                for field, kind in fields.items()
            },
        }
        if role in ("tool_icons", "text_sizes"):
            schema[role].update({
                "key_pattern": _TOOL_NAME_PATTERN if role == "tool_icons" else _TEXT_SIZE_NAME_PATTERN,
                "reserved_keys": list(_RESERVED_NAMES) + (list(TEXT_SIZES) if role == "text_sizes" else []),
                "dynamic": True,
            })
    return schema


def _pixels(value: str, minimum: int, maximum: int, *, zero: bool = False) -> bool:
    if zero and value == "0":
        return True
    return bool(re.fullmatch(r"-?[0-9]{1,3}px", value)) and minimum <= int(value[:-2]) <= maximum


def _valid_value(kind: str, value: Any) -> bool:
    spec = _KIND_SPECS[kind]
    if kind == "platform_text_size":
        return isinstance(value, dict) and bool(value) and all(
            platform in spec["properties"] and isinstance(size, str) and size in PLATFORM_TEXT_SIZES
            for platform, size in value.items()
        )
    expected = {"string": str, "integer": int, "boolean": bool}[spec["type"]]
    if type(value) is not expected:
        return False
    if expected is bool:
        return True
    if expected is int:
        return value in spec["choices"] if "choices" in spec else (
            value >= spec.get("min", value) and value <= spec.get("max", value)
        )
    if len(value) > spec.get("max_length", 128) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    if "choices" in spec:
        if value in spec["choices"]:
            return True
        if not spec.get("allow_custom"):
            return False
    if "pattern" in spec:
        return re.fullmatch(spec["pattern"], value) is not None
    if kind == "color":
        match = re.fullmatch(_RGBA_PATTERN, value)
        return match is not None and all(int(c) <= 255 for c in match.groups()[:3])
    if kind == "separator":
        return not any(c in value for c in "<>&")
    if kind in ("padding", "margin", "icon_size"):
        parts = value.split(" ")
        return len(parts) in spec["arity"] and all(
            _pixels(part, spec["min"], spec["max"], zero=kind != "icon_size") for part in parts
        )
    if kind == "column_width" and re.fullmatch(r"[0-9]{1,3}%", value):
        return spec["percent_min"] <= int(value[:-1]) <= spec["percent_max"]
    return _pixels(value, spec["min"], spec["max"])


def validate_style_role(
    role: str, values: Any, strict: bool = True, *, text_sizes: Any = None,
) -> dict[str, Any]:
    """Validate explicit overrides, raising ValueError or filtering invalid fields.

    Error messages name registered fields only; submitted values remain private.
    Runtime filtering isolates malformed sections and retains valid siblings.
    """
    if not isinstance(role, str) or role not in STYLE_FIELDS:
        if strict:
            raise ValueError("Unknown style role")
        return {}
    if not isinstance(values, dict):
        if strict:
            raise ValueError(f"streaming.{role} must be an object")
        return {}
    custom_sizes = (validate_style_role("text_sizes", text_sizes, strict=False)
                    if role != "text_sizes" and text_sizes else {})
    result = {}
    fields = STYLE_FIELDS[role]
    for field, value in values.items():
        if role in ("tool_icons", "text_sizes"):
            known = (
                isinstance(field, str) and field not in _RESERVED_NAMES
                and re.fullmatch(_TOOL_NAME_PATTERN, field) is not None
            )
            kind = "icon_token" if role == "tool_icons" else "platform_text_size"
            if role == "text_sizes":
                known = (isinstance(field, str) and field not in _RESERVED_NAMES and field not in TEXT_SIZES
                         and re.fullmatch(_TEXT_SIZE_NAME_PATTERN, field) is not None)
        else:
            known = isinstance(field, str) and field in fields
            kind = fields[field] if known else ""
        if not known:
            if strict:
                raise ValueError(f"Unknown field in streaming.{role}")
            continue
        valid = ((isinstance(value, str) and (value in TEXT_SIZES or value in custom_sizes))
                 if kind == "text_size" else _valid_value(kind, value))
        if not valid:
            if strict:
                location = f"{role} entry" if role in ("tool_icons", "text_sizes") else f"{role}.{field}"
                raise ValueError(f"Invalid style value for streaming.{location}")
            continue
        result[field] = deepcopy(value)
    return result
