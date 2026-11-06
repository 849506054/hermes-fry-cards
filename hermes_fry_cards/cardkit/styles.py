"""Snapshot-scoped presentation helpers shared by initial and replacement writes."""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from typing import Any

from ..config import Config

_style_config: ContextVar[Config | None] = ContextVar("card_style_config", default=None)


def current_config() -> Config:
    """Resolve the active card snapshot, or capture one for a standalone call."""
    cfg = _style_config.get()
    return cfg if cfg is not None else Config().card_snapshot()


@contextmanager
def style_scope(config: Config | None = None):
    """Inherit an outer card's scope and restore it even if a builder raises."""
    token = _style_config.set(config if config is not None else current_config())
    try:
        yield _style_config.get()
    finally:
        _style_config.reset(token)


def styled_builder(fn):
    """Supply one scoped Config to a synchronous builder and all its helpers."""
    sig = signature(fn)

    @wraps(fn)
    def wrapped(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        with style_scope(bound.arguments.get("config")) as cfg:
            assert cfg is not None
            bound.arguments["config"] = cfg
            if fn.__name__ in {"build_streaming_card_v2", "build_cron_card", "build_background_card"}:
                from ..style_options import STYLE_FIELDS

                overrides = {role: sorted(values) for role in STYLE_FIELDS
                             if isinstance(values := cfg.style_options(role), dict) and values}
                logging.getLogger("hermes_fry_cards").info(
                    "CardKit style: builder=%s overrides=%s", fn.__name__, overrides,
                )
            return fn(*bound.args, **bound.kwargs)

    return wrapped


def option(role: str, field: str, fallback: Any = None) -> Any:
    """Read explicit validated overrides; retain a consumer's existing default."""
    return current_config().style_options(role).get(field, fallback)


def apply_options(element: dict, role: str, fields: tuple[str, ...], *, prefix: str = "") -> dict:
    """Copy only explicitly configured fields to their native payload location."""
    result = dict(element)
    values = current_config().style_options(role)
    for field in fields:
        key = prefix + field
        if key in values:
            result[field] = values[key]
    return result


def _format_line(line: str, color: str | None, bold: bool, italic: bool) -> str:
    # Keep block prefixes and whitespace outside inline wrappers.
    match = re.match(r"^((?:[ \t]*(?:#{1,6}|>|[-+*]|\d+[.)])[ \t]+)*[ \t]*)(.*?)(\s*)$", line, re.DOTALL)
    if match is None or not match[2]:
        return line
    prefix, text, suffix = match.groups()
    outer = re.fullmatch(r"<font color=['\"]([^'\"]+)['\"]>(.*)</font>", text, re.DOTALL)
    if outer and _is_outer_font(text):
        color = color or outer[1]
        text = outer[2]
    if bold or italic:
        text = _emphasize(text, bold, italic)
    if color:
        text = f"<font color='{color}'>{text}</font>"
    return prefix + text + suffix


def _is_outer_font(text: str) -> bool:
    depth = 0
    for tag in re.finditer(r"<font\b[^>]*>|</font>", text):
        depth += -1 if tag[0] == "</font>" else 1
        if depth == 0:
            return tag.end() == len(text)
    return False


_INLINE_TOKEN = re.compile(
    r"(?<!\*)(\*{1,3})(?!\*)(.+?)(?<!\*)\1(?!\*)|<[^>]+>|`+[^`]+`+|!?\[[^\]\n]*\]\([^\n)]*\)"
)


def _emphasize(text: str, bold: bool, italic: bool) -> str:
    def wrap(value: str, weight: bool = bold, slant: bool = italic) -> str:
        match = re.fullmatch(r"(\s*)(.*?)(\s*)", value, re.DOTALL)
        if not match or not re.search(r"\w", match[2]):
            return value
        marker = "***" if weight and slant else "**" if weight else "*" if slant else ""
        return match[1] + marker + match[2] + marker + match[3]

    parts = []
    position = 0
    for token in _INLINE_TOKEN.finditer(text):
        parts.append(wrap(text[position:token.start()]))
        if token[1]:
            count = len(token[1])
            parts.append(wrap(token[2], bold or count >= 2, italic or count in (1, 3)))
        else:
            parts.append(token[0])
        position = token.end()
    parts.append(wrap(text[position:]))
    return "".join(parts)


def text_content(content: str, role: str, *, markdown: bool = True) -> str:
    """Apply explicit inline typography while retaining fences and table syntax."""
    if not markdown or not content:
        return content
    values = current_config().style_options(role)
    color = values.get("text_color")
    bold, italic = values.get("bold", False), values.get("italic", False)
    if not (color or bold or italic):
        return content
    # Lazy import keeps the markdown optimizer and its formatter in one scope.
    from .markdown import _scan_markdown_blocks

    def plain(block: str) -> str:
        lines = []
        fence = ""
        for line in block.splitlines(keepends=True):
            inner = re.sub(r"^(?:[ \t]*>[ \t]?)+", "", line)
            marker = re.match(r"^[ \t]*(`{3,}|~{3,})", inner)
            if marker:
                token = marker[1]
                if not fence:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = ""
                lines.append(line)
            elif (fence or inner.startswith(("    ", "\t"))
                  or re.fullmatch(r"[ \t]*(?:[-*_][ \t]*){3,}[\r\n]*", inner)
                  or re.fullmatch(r"[ \t]*(?:=+|-+)[ \t]*[\r\n]*", inner)):
                lines.append(line)
            else:
                lines.append(_format_line(line, color, bold, italic))
        return "".join(lines)

    output = "".join(
        block.text if block.kind in {"fence", "table"} else plain(block.text)
        for block in _scan_markdown_blocks(content)
    )
    from .markdown import _ANSWER_MAX_BYTES, _REASONING_MAX_BYTES, clamp_utf8

    budget = _REASONING_MAX_BYTES if role == "reasoning" else _ANSWER_MAX_BYTES
    if len(output.encode("utf-8")) > budget:
        # Optional decoration yields to the existing content budget; preserve text.
        return clamp_utf8(content, max_bytes=budget)
    return output


def text_element(element: dict, role: str) -> dict:
    """Style a semantic text element without changing its tag, ID or data."""
    result = dict(element)
    if result.get("tag") == "div":
        result = apply_options(result, role, ("margin",))
        if isinstance(result.get("text"), dict):
            result["text"] = text_element(result["text"], role)
        return result
    tag = result.get("tag")
    if tag not in {"markdown", "lark_md", "plain_text"}:
        return result
    fields = ("text_size", "text_color", "text_align") if tag == "plain_text" else ("text_size", "text_align")
    if tag == "plain_text" and role in {"process_title", "reasoning_title"}:
        fields = ("text_size", "text_color")
    if tag == "markdown":
        fields += ("margin",)
    result = apply_options(result, role, fields)
    if tag != "plain_text":
        if isinstance(result.get("content"), str):
            result["content"] = text_content(result["content"], role)
        if isinstance(result.get("i18n_content"), dict):
            result["i18n_content"] = {
                lang: text_content(text, role) if isinstance(text, str) else text
                for lang, text in result["i18n_content"].items()
            }
    return result


def icon_element(icon: dict | None, role: str) -> dict | None:
    """Build a standard/custom icon only when configured or already present."""
    values = current_config().style_options(role)
    result = dict(icon) if icon else None
    if values.get("icon_img_key"):
        result = {"tag": "custom_icon", "img_key": values["icon_img_key"]}
        if icon and "size" in icon:
            result["size"] = icon["size"]
    elif values.get("icon_token"):
        result = {**(result or {}), "tag": "standard_icon", "token": values["icon_token"]}
        result.pop("img_key", None)
    if result is not None:
        if "icon_color" in values and result.get("tag") == "standard_icon":
            result["color"] = values["icon_color"]
        if "icon_size" in values:
            result["size"] = values["icon_size"]
    return result


def header_element(header: dict) -> dict:
    """Native card titles retain client typography; style their container/icon."""
    result = apply_options(header, "header", ("padding",))
    padding = result.get("padding", "").split()
    if len(padding) == 2:
        result["padding"] = " ".join(padding * 2)
    icon = icon_element(result.get("icon"), "header")
    if icon is not None:
        result["icon"] = icon
    return result


def panel_element(panel: dict, *, completed: bool = False, status: str = "completed") -> dict:
    """Style a native process panel while leaving position and expansion intact."""
    result = apply_options(panel, "process", (
        "padding", "margin", "vertical_spacing", "horizontal_spacing", "horizontal_align", "vertical_align",
        "background_color",
    ))
    if completed and not option("process", "completed_border", True):
        result.pop("border", None)
    elif "border" in result:
        border = dict(result["border"])
        if completed:
            border["color"] = option("process", status + "_color", border.get("color"))
        else:
            border["color"] = option("process", "border_color", border.get("color"))
        border["corner_radius"] = option("process", "border_radius", border.get("corner_radius"))
        result["border"] = border
    return result
