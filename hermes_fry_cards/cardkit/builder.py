"""CardKit v2.0 卡片构建器 — i18n、元素构建、卡片组装."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..config import Config
from ..streaming.segments import Segment, SegmentType
from ..streaming.tooluse import ToolDisplayStep
from .i18n import _LOCALES, _T, _content_lang, _content_t, _i18n, _t
from .markdown import (
    _REASONING_MAX_BYTES,
    _downgrade_tables,
    _split_long_text,
    clamp_utf8,
    optimize_markdown_style,
    parse_table_block,
    split_markdown_tables,
)
from .styles import (
    apply_options,
    current_config,
    header_element,
    icon_element,
    option,
    panel_element,
    styled_builder,
    text_element,
)

STREAMING_ELEMENT_ID = "streaming_content"
REASONING_ELEMENT_ID = "reasoning_content"
REASONING_TEXT_ELEMENT_ID = "reasoning_text"
TOOL_PANEL_ELEMENT_ID = "tool_panel"
UNIFIED_PANEL_ELEMENT_ID = "unified_panel"
# 流式和完成态共用思考时间线外框。
PROCESS_PANEL_ELEMENT_ID = UNIFIED_PANEL_ELEMENT_ID
PROCESS_ANCHOR_ELEMENT_ID = "process_anchor"
BODY_SURFACE_ELEMENT_ID = "fry_body_surface"
BODY_SURFACE_ELEMENT_COUNT = 2
BODY_SURFACE_COLOR = "cus-0"
BODY_SURFACE_DEEP_COLOR = "cus-1"
LOADING_ELEMENT_ID = "loading_icon"
LOADING_CURSOR_ELEMENT_ID = "loading_cursor"
_LOADING_ELEMENT_ID = LOADING_ELEMENT_ID  # 兼容旧私有引用
_LOADING_IMG_KEY = "img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg"

# 工具面板最多渲染的步骤数（#46）：50+ 步长任务时整卡 JSON 随步数线性膨胀
# （实测 50 步 ≈ 37KB），10 Hz 整卡重绘会卡死飞书客户端。header 保留总步数，
# 更早的步骤折叠成一行摘要；估算端（segment_helper.estimate_tool_elements）按同一上限取值。
MAX_TOOL_PANEL_STEPS = 30


@styled_builder
def _build_body(elements: list[dict], *, config: Config | None = None) -> dict:
    """Place reply content on a theme-aware surface, retaining its element IDs."""
    if any(element.get("tag") == "table" for element in elements):
        root: list[dict] = []
        group: list[dict] = []
        for element in [*elements, None]:
            if element is not None and element.get("tag") != "table":
                group.append(element)
                continue
            if group:
                surface = _build_body(group)["elements"][0]
                surface["element_id"] = f"{BODY_SURFACE_ELEMENT_ID}_{len(root)}"
                root.append(surface)
                group = []
            if element is not None:
                root.append(element)
        return _body_layout({"elements": root})
    surface = {
        "tag": "column_set", "element_id": BODY_SURFACE_ELEMENT_ID,
        "flex_mode": "none", "background_style": BODY_SURFACE_COLOR,
        "columns": [{
            "tag": "column", "width": "weighted", "weight": 1,
            "vertical_align": "top", "elements": elements,
        }],
    }
    surface = apply_options(surface, "body", ("horizontal_spacing", "horizontal_align"))
    surface = apply_options(surface, "body", ("margin",), prefix="surface_")
    surface["columns"][0] = apply_options(surface["columns"][0], "body", ("padding",), prefix="surface_")
    surface["columns"][0] = apply_options(surface["columns"][0], "body", (
        "vertical_spacing", "horizontal_align", "vertical_align",
    ))
    return _body_layout({"elements": [surface]})


def _body_layout(body: dict) -> dict:
    return apply_options(body, "body", ("padding",), prefix="body_")


@styled_builder
def _body_style(config: Config | None = None) -> dict:
    """Resolve the same body surface independently for light and dark clients."""
    cfg = config or current_config()
    def rgba(value: str) -> str:
        channels = (int(value[i:i + 2], 16) for i in (1, 3, 5))
        return "rgba(" + ",".join(map(str, channels)) + ",1)"
    def deeper_hex(value: str, factor: float) -> str:
        """One shade below the given colour, kept inside the 0-255 range."""
        channels = (min(255, round(int(value[i:i + 2], 16) * factor)) for i in (1, 3, 5))
        return "#" + "".join(f"{channel:02x}" for channel in channels)
    style = {"color": {
        BODY_SURFACE_COLOR: {
            "light_mode": rgba(cfg.body_light_color),
            "dark_mode": rgba(cfg.body_dark_color),
        },
        BODY_SURFACE_DEEP_COLOR: {
            "light_mode": rgba(option("body", "deep_light_color")
                               or deeper_hex(cfg.body_light_color, 0.94)),
            "dark_mode": rgba(option("body", "deep_dark_color")
                              or deeper_hex(cfg.body_dark_color, 0.75)),
        },
    }}
    text_sizes = cfg.style_options("text_sizes")
    if isinstance(text_sizes, dict) and text_sizes:
        style["text_size"] = text_sizes
    return style


def body_content_elements(card: dict[str, Any]) -> list[dict]:
    """Read content in display order; a single surface retains its mutable list."""
    body = card.get("body")
    elements = body.get("elements") if isinstance(body, dict) else None
    if not isinstance(elements, list):
        return []
    if len(elements) == 1 and elements[0].get("element_id") == BODY_SURFACE_ELEMENT_ID:
        return elements[0]["columns"][0]["elements"]
    if elements and elements[0].get("element_id") == BODY_SURFACE_ELEMENT_ID and not any(
        str(element.get("element_id", "")).startswith(BODY_SURFACE_ELEMENT_ID + "_")
        for element in elements[1:]
    ):
        return [*elements[0]["columns"][0]["elements"], *elements[1:]]
    if any(str(element.get("element_id", "")).startswith(BODY_SURFACE_ELEMENT_ID + "_")
           for element in elements):
        flat = []
        for element in elements:
            if str(element.get("element_id", "")).startswith(BODY_SURFACE_ELEMENT_ID + "_"):
                flat.extend(element["columns"][0]["elements"])
            else:
                flat.append(element)
        return flat
    return elements


def _truncate_model(name: str) -> str:
    """截断模型名：or/lc/LongCat-2.0 → ⇲LongCat-2.0（保留最后一段）.

    如果开启 truncate_model_name 才调用；未开启时原样返回。
    """
    if not name:
        return name
    parts = name.split("/")
    if len(parts) > 1:
        return f"⇲{parts[-1]}"
    return name


@styled_builder
def _display_model(name: str, *, config: Config | None = None) -> str:
    """模型显示名：别名优先（~/.hermes/model_aliases.json 子串匹配，支持时段人设），未命中回落截断.

    别名文件由 Config().model_aliases() 每次重读（热更新）；对象条目读取时按北京时间
    解析为当前时段显示名。model_aliases_enabled 关闭时整体忽略别名回落截断。
    """
    if not name:
        return name

    cfg = current_config()
    if cfg.model_aliases_enabled:
        aliases = cfg.model_aliases()
        lowered = name.lower()
        for key, alias in aliases.items():
            if key and key in lowered:
                return alias
    if cfg.truncate_model_name:
        return _truncate_model(name)
    return name


@styled_builder
def _panel_header(
    title_el: dict, *, icon_position: str = "right", position: str = "bottom",
    arrow: bool | None = None, config: Config | None = None,
) -> dict:
    """构造完整原生标题栏，按运行中或封卡的配置选择展开图标。

    ``partial_update_element`` 按整块覆盖 header；位置、文字与当前态图标共用本构造器。
    """
    header = {
        "title": title_el,
        "vertical_align": "center",
        "position": position,
    }
    if arrow is False:
        show_arrow = option("process", "completed_arrow", False)
    elif arrow is True:
        show_arrow = True
    else:
        show_arrow = option("process", "arrow", True)
    if show_arrow:
        icon_el = {
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined",
            "size": "16px 16px",
        }
        if icon_position == "right":
            icon_el["color"] = "grey"
        header["icon"] = icon_element(icon_el, "process")
        header["icon_position"] = option("process", "icon_position", icon_position)
        header["icon_expanded_angle"] = option("process", "icon_expanded_angle", -180)
    return apply_options(header, "process", ("padding", "background_color", "vertical_align", "width"),
                         prefix="header_")


@styled_builder
def _process_title(
    reasoning_count: int = 0, tool_count: int = 0, *, element_id: str | None = None,
    tool_mode: bool = False, config: Config | None = None,
) -> dict:
    def counts(locale: int) -> str:
        reasoning = _T["reasoning_count_streaming"][locale].format(reasoning_count)
        tools = _T["tool_count_streaming"][locale].format(tool_count)
        if tool_mode:
            return tools + " · " + reasoning.removeprefix("💭 ")
        return reasoning + " · " + tools.removeprefix("🔧 ")

    en, zh = (counts(locale) for locale in (0, 1))
    if element_id:
        content = en if _content_lang() == "en" else zh
        return text_element({"tag": "plain_text", "element_id": element_id, "content": content,
                "text_color": "grey", "text_size": "notation"}, "process_title")
    return text_element({"tag": "plain_text", "content": en, "i18n_content": _i18n(en, zh),
            "text_color": "grey", "text_size": "notation"}, "process_title")


def _completed_counts_element(reasoning_count: int, tool_count: int) -> dict:
    """封卡详情框首行：思考轮数与工具执行步数，文案沿用更新期的计数形态.

    0 轮 0 步照常写出来，这一行本身就是框内内容，不再另补占位句。
    框内那层 column_set 只收 markdown（plain_text 会被飞书判 10002 拒绝），
    所以走 process summary 的灰字 markdown 形态。
    """
    en_tpl, zh_tpl = _T["process_counts_done"]
    en = en_tpl.format(max(0, reasoning_count), max(0, tool_count))
    zh = zh_tpl.format(max(0, reasoning_count), max(0, tool_count))
    return _build_process_summary(en, zh_text=zh)


@styled_builder
def _collapsible_panel(
    *,
    expanded: bool,
    title_el: dict,
    elements: list[dict],
    vertical_spacing: str = "4px",
    icon_position: str = "right",
    config: Config | None = None,
) -> dict:
    return panel_element({
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": _panel_header(title_el, icon_position=icon_position),
        "border": {"color": "grey", "corner_radius": "5px"},
        "vertical_spacing": vertical_spacing,
        "padding": "8px 8px 8px 8px",
        "elements": elements,
    })


@styled_builder
def _streaming_element(
    content: str = "",
    *,
    element_id: str = STREAMING_ELEMENT_ID,
    text_size: str = "normal_v2",
    config: Config | None = None,
) -> dict:
    return text_element({
        "tag": "markdown",
        "content": content,
        "text_align": "left",
        "text_size": text_size,
        "margin": "0px 0px 0px 0px",
        "element_id": element_id,
    }, "body")


_HEADER_STATES: dict[str, dict[str, str]] = {
    "streaming": {"i18n_key": "processing_prefix"},
    "completed": {"i18n_key": "status_completed"},
    "error": {"i18n_key": "status_error"},
    "stopped": {"i18n_key": "status_stopped"},
}


@styled_builder
def _build_header(status: str, *, config: Config | None = None) -> dict[str, Any]:
    """构建默认主题的状态标题，保留文案与多语言信息."""
    cfg = _HEADER_STATES.get(status, _HEADER_STATES["completed"])
    resolved = config or current_config()
    image_key = resolved.header_icons.get(status)
    text_key = cfg["i18n_key"] + "_plain" if image_key else cfg["i18n_key"]
    en_text, zh_text = _T[text_key]
    header = {
        "title": {
            "tag": "plain_text",
            "content": en_text,
            "i18n_content": _i18n(en_text, zh_text),
        },
    }
    if image_key:
        header["icon"] = {"tag": "custom_icon", "img_key": image_key}
    template = resolved.header_template
    if template != "default":
        header["template"] = template
    return header_element(header)


@styled_builder
def build_notice_header(*, config: Config | None = None, title_key: str = "notice") -> dict[str, Any]:
    """The shared text title for standalone system notices."""
    cfg = config or current_config()
    en, zh = _T[title_key]
    header: dict[str, Any] = {"title": {"tag": "plain_text", "content": en, "i18n_content": _i18n(en, zh)}}
    if cfg.header_template != "default":
        header["template"] = cfg.header_template
    return header_element(header)


_NOTICE_ELEMENT_ID = "fry_notice"
# 公开别名：控制器建卡时要把这个 id 登记给段状态（首段正文原地接管提示元素）。
NOTICE_ELEMENT_ID = _NOTICE_ELEMENT_ID


def notice_element_id(index: int) -> str:
    """第 ``index`` 条提示的元素 id：``fry_notice`` / ``fry_notice_1`` / ``fry_notice_2`` …

    完成态可能同时命中多条提示（重定向 + 后台回执），`notices` 只按文本去重，
    不同文本会各占一个元素；固定 id 会让卡里出现重复 element_id（#43 评审）。
    """
    return _NOTICE_ELEMENT_ID if index == 0 else f"{_NOTICE_ELEMENT_ID}_{index}"


@styled_builder
def build_notice_elements(texts: list[str] | None, *, offset: int = 0, config: Config | None = None) -> list[dict]:
    """通知区元素列表：每条提示一个元素，id 按序号派生，保证卡内唯一.

    ``offset``：顶部区与底部区共存时，底部元素 id 接在顶部序号之后派生，
    避免两个区撞同一个 ``fry_notice``（#43 反馈）。
    """
    return [
        build_notice_element(text, element_id=notice_element_id(offset + index))
        for index, text in enumerate(texts or [])
    ]


@styled_builder
def build_notice_element(text: str, *, element_id: str = _NOTICE_ELEMENT_ID, config: Config | None = None) -> dict:
    """通知区的单条提示（Hermes 原文，原样渲染）.

    文案不新增也不包装（#43 反馈）：忙碌回执 / 重定向文案本身已是完整一句话，
    再套「💡 提示」标签反而像一段新内容。原样写入；视觉区分由位置（底部通知位）
    承担，不写进 content。
    """
    return text_element({
        "tag": "markdown",
        "element_id": element_id,
        "content": text,
    }, "body")


@styled_builder
def build_streaming_notice_element(
    text: str, *, element_id: str, text_size: str = "normal_v2", config: Config | None = None,
) -> dict:
    """流式态通知槽元素（与建卡时写入的提示元素同构）.

    走流式元素形态（固定 id + 正文字号），所以首段正文能在同一个元素上原地覆盖；
    回合中途到达的回执由控制器用同一形态补画到 loading 之前，视觉位置与建卡时一致。
    """
    return _streaming_element(content=text, element_id=element_id, text_size=text_size)


@styled_builder
def _loading_element(*, config: Config | None = None) -> dict:
    """插入锚点：留在正文底色块内，供新增段落与通知按顺序插到它之前。

    视觉上一片空白、不占行高；用户看到的转圈光标由 ``_loading_cursor_element``
    画在底色块外面。
    """
    return {
        "tag": "markdown",
        "content": "",
        "element_id": _LOADING_ELEMENT_ID,
    }


@styled_builder
def _loading_cursor_element(*, config: Config | None = None) -> dict:
    """流式光标：落在正文底色块下方、卡片底色的区域里。"""
    return {
        "tag": "markdown",
        "content": " ",
        "icon": {
            "tag": "custom_icon",
            "img_key": option("loading", "img_key", _LOADING_IMG_KEY),
            "size": option("loading", "size", "16px 16px"),
        },
        "element_id": LOADING_CURSOR_ELEMENT_ID,
    }


_TABLE_PAGE_SIZE = 10
_TABLE_ROW_MAX_HEIGHT = "999px"


@styled_builder
def _append_content_elements(
    elements: list[dict], content: str, *, text_size: str | None = None, config: Config | None = None,
) -> None:
    """正文 → 元素列表：文本片段出 markdown 元素，表格片段出原生 table 元素.

    仅传字符串/纯文本组件的卡片（完成态、cron、后台推送）共用此入口，
    保证同一个 markdown 表格在任何卡片里都渲染成同一种元素。
    """
    content = _downgrade_tables(optimize_markdown_style(content))
    content = clamp_utf8(content, preserve_tail=True)
    for kind, part in split_markdown_tables(content):
        if kind == "table":
            parsed = parse_table_block(part)
            if parsed:
                elements.append(_build_table_element(*parsed))
                continue
        for chunk in _split_long_text(part):
            if not chunk.strip():
                continue
            element: dict[str, Any] = {"tag": "markdown", "content": chunk}
            if text_size:
                element["text_size"] = text_size
            elements.append(text_element(element, "body"))


@styled_builder
def _build_table_element(header: list[str], rows: list[list[str]], *, config: Config | None = None) -> dict[str, Any]:
    """飞书原生 table 元素 — 列宽 auto + 行高 auto.

    markdown 表格不支持设置列宽/行高：长单元格折行后被固定行高裁掉。
    原生表格可设 `columns[].width: auto`（按内容给宽）与 `row_height: auto`，
    长文本完整显示；三列总宽超出画布时表格左右可滑动。
    """
    columns = [
        {
            "name": f"c{index}",
            "display_name": name,
            "data_type": "lark_md",
            "width": option("table", "column_width", "auto"),
        }
        for index, name in enumerate(header)
    ]
    columns = [apply_options(col, "table", ("horizontal_align", "vertical_align")) for col in columns]
    table_rows = [
        {
            f"c{index}": row[index] if index < len(row) else ""
            for index in range(len(header))
        }
        for row in rows
    ]
    table = {
        "tag": "table",
        "page_size": _TABLE_PAGE_SIZE,
        "row_height": "auto",
        "row_max_height": _TABLE_ROW_MAX_HEIGHT,
        "header_style": {
            "text_align": "left",
            "text_size": "notation",
            "background_style": "none",
            "text_color": "default",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": table_rows,
    }
    table["header_style"] = apply_options(table["header_style"], "table", (
        "text_align", "text_size", "background_style", "text_color", "bold", "lines",
    ), prefix="header_")
    return apply_options(table, "table", ("page_size", "row_height", "row_max_height",
                                          "freeze_first_column", "margin"))


@styled_builder
def _build_tool_panel(
    steps: list[ToolDisplayStep],
    elapsed_ms: float = 0,
    *,
    expanded: bool = True,
    element_id: str | None = TOOL_PANEL_ELEMENT_ID,
    running: bool = False,
    compact: bool = False,
    config: Config | None = None,
) -> dict:
    en_t, zh_t = _T["tool_running"] if running else _T["tool_use"]
    en_parts, zh_parts = [en_t], [zh_t]
    if steps:
        tpl_en, tpl_zh = _T["steps"]
        en_parts.append(tpl_en.format(len(steps), "s" if len(steps) > 1 else ""))
        zh_parts.append(tpl_zh.format(len(steps), ""))
    if elapsed_ms > 0:
        en_parts.append(f"({_format_elapsed(elapsed_ms)})")
        zh_parts.append(f"({_format_elapsed(elapsed_ms)})")

    children: list[dict] = []
    # 面板只渲染最近 N 步（#46）：全部步骤都进面板时卡片体积随任务长度无界增长，
    # 10 Hz 整卡重绘会把飞书客户端渲染打满。header 保留总步数，更早的折叠成一行摘要。
    shown = steps
    omitted = 0
    if len(steps) > MAX_TOOL_PANEL_STEPS:
        omitted = len(steps) - MAX_TOOL_PANEL_STEPS
        shown = steps[-MAX_TOOL_PANEL_STEPS:]
        tpl_en, tpl_zh = _T["tool_omitted"]
        summary_en = tpl_en.format(omitted)
        summary_zh = tpl_zh.format(omitted)
        children.append(text_element({
            "tag": "markdown",
            "content": summary_en,
            "i18n_content": _i18n(summary_en, summary_zh),
            "text_size": "notation",
        }, "summary"))
    for s in shown:
        # 紧凑模式（#53）：每步只渲染标题行（工具名 · 状态），不渲染入参与输出。
        # 完成态整卡重建会把面板子元素连同流式期已提交的一并累计，CardKit 按
        # 「已有 + 本次」计数，30 步全量面板足以越过 200 硬上限 → 300305。
        if compact:
            children.append(_build_tool_step_title(s))
        else:
            children.extend(_build_tool_step_elements(s))

    panel = _collapsible_panel(
        expanded=expanded,
        title_el=text_element({
            "tag": "plain_text",
            "content": f"🔧 {' · '.join(en_parts)}",
            "i18n_content": _i18n(f"🔧 {' · '.join(en_parts)}", f"🔧 {' · '.join(zh_parts)}"),
            "text_color": "grey",
            "text_size": "notation",
        }, "process_title"),
        elements=children,
    )
    if element_id:
        panel["element_id"] = element_id
    if running and not (config or current_config()).process_border:
        panel.pop("border")
    return panel


@styled_builder
def _build_tool_step_elements(step: ToolDisplayStep, *, config: Config | None = None) -> list[dict]:
    elements: list[dict] = [_build_tool_step_title(step)]
    detail = _build_tool_step_detail(step)
    if detail:
        elements.append(detail)
    output = _build_tool_step_output(step)
    if output:
        elements.append(output)
    return elements


@styled_builder
def _build_tool_step_title(step: ToolDisplayStep, *, config: Config | None = None) -> dict:
    status = step.get("status", "running")
    status_info = _tool_status_info(status)
    title = step.get("title", step.get("name", "tool"))
    title_text = _escape_md(title)
    if option("tool_title", "bold", True):
        title_text = f"**{title_text}**"
    content = f"{title_text} · <font color='{status_info['color']}'>{status_info['label']}</font>"
    return text_element({
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": option("tool_icons", step.get("name", ""), step.get("icon", "tool_02")),
            "color": option("tool_title", "icon_color", "grey"),
        },
        "text": {
            "tag": "lark_md",
            "content": content,
            "text_size": "notation",
        },
    }, "tool_title")


@styled_builder
def _build_tool_step_detail(step: ToolDisplayStep, *, config: Config | None = None) -> dict | None:
    detail = step.get("detail", "").strip()
    if not detail:
        return None
    return text_element({
        "tag": "div",
        "margin": "0px 0px 0px 22px",
        "text": {
            "tag": "plain_text",
            "content": detail,
            "text_color": "grey",
            "text_size": "notation",
        },
    }, "tool_detail")


@styled_builder
def _build_tool_step_output(step: ToolDisplayStep, *, config: Config | None = None) -> dict | None:
    error_block = step.get("error_block")
    result_block = step.get("result_block")

    lines: list[str] = []
    if error_block:
        lines.append("**Error**")
        lines.append(
            error_block.get("fenced")
            or _format_code_block(error_block.get("content", ""), error_block.get("language", "text"))
        )
    elif result_block:
        lines.append("**Result**")
        lines.append(
            result_block.get("fenced")
            or _format_code_block(result_block.get("content", ""), result_block.get("language", "json"))
        )

    if not lines:
        return None

    return text_element({
        "tag": "div",
        "margin": "0px 0px 0px 22px",
        "text": {
            "tag": "lark_md",
            "content": "\n".join(lines),
            "text_size": "notation",
        },
    }, "tool_output")


@styled_builder
def _tool_status_info(status: str, *, config: Config | None = None) -> dict[str, str]:
    info = {
        "running": {"label": "Running", "color": "turquoise"},
        "success": {"label": "Succeeded", "color": "green"},
        "error": {"label": "Failed", "color": "red"},
    }.get(status, {"label": status.capitalize(), "color": "grey"})
    field = status if status in {"running", "success", "error"} else "unknown"
    info["color"] = option("tool_title", field + "_color", info["color"])
    return info


def _format_code_block(content: str, language: str) -> str:
    normalized = content.replace("\r\n", "\n").strip()
    fence = "`" * max(3, _longest_backtick_run(normalized) + 1)
    return f"{fence}{language}\n{normalized}\n{fence}"


def _longest_backtick_run(value: str) -> int:
    matches = re.findall(r"`+", value)
    return max((len(m) for m in matches), default=0)


def _escape_md(value: str) -> str:
    return re.sub(r"([`*_{}\[\]<>])", r"\\\1", value.replace("\\", "\\\\"))


@styled_builder
def _build_reasoning_stage(
    text: str, elapsed_ms: float = 0, *, element_id: str | None = None,
    text_element_id: str | None = None,
    config: Config | None = None,
) -> list[dict[str, Any]]:
    """时间线阶段：耗时标题和正文直接作为外框的相邻子元素。"""
    label = (
        _content_t("thought_for").format(_format_elapsed(elapsed_ms))
        if elapsed_ms > 0 else _content_t("thinking_panel")
    )
    return [text_element(element, role) for element, role in zip([
        {
            "tag": "markdown", "content": f"<font color='grey'>💭 {label}</font>",
            "text_size": "notation",
            **({"element_id": element_id} if element_id else {}),
        },
        {
            "tag": "markdown", "content": text or " ", "text_size": "notation",
            **({"element_id": text_element_id} if text_element_id else {}),
        },
    ], ("reasoning_title", "reasoning"), strict=True)]


@styled_builder
def _build_process_summary(
    text: str, *, zh_text: str | None = None, element_id: str | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Build a styled summary element for the component style API."""
    content = f"<font color='grey'>{text}</font>"
    element: dict[str, Any] = {"tag": "markdown", "content": content, "text_size": "notation"}
    if zh_text is not None:
        element["i18n_content"] = _i18n(content, f"<font color='grey'>{zh_text}</font>")
    if element_id:
        element["element_id"] = element_id
    return text_element(element, "summary")


@styled_builder
def _build_reasoning_panel(
    text: str, elapsed_ms: float = 0, *, expanded: bool = False, element_id: str | None = None,
    text_element_id: str | None = REASONING_TEXT_ELEMENT_ID,
    config: Config | None = None,
) -> dict:
    if elapsed_ms > 0:
        d = _format_elapsed(elapsed_ms)
        en_label, zh_label = _T["thought_for"][0].format(d), _T["thought_for"][1].format(d)
    elif not text.strip():
        en_label, zh_label = _T["thinking_panel"]
    else:
        en_label, zh_label = _T["thought"]
    panel = _collapsible_panel(
        expanded=expanded,
        title_el=text_element({
            "tag": "plain_text",
            "content": f"💭 {en_label}",
            "i18n_content": _i18n(f"💭 {en_label}", f"💭 {zh_label}"),
            "text_color": "grey",
            "text_size": "notation",
        }, "reasoning_title"),
        elements=[text_element({
            "tag": "markdown",
            "content": text,
            "text_size": "notation",
            **({"element_id": text_element_id} if text_element_id else {}),
        }, "reasoning")],
        vertical_spacing="8px",
    )
    if element_id:
        panel["element_id"] = element_id
    return panel


def _render_panel_field(
    name: str,
    data: dict,
    *,
    reasoning_count: int,
    tool_count: int,
    panel_elapsed_ms: int | float,
    show_context: bool,
    context_mode: str,
) -> tuple[str | None, str | None]:
    """统一面板 header 字段渲染 — 与 footer 共用字段池，外加面板专属计数.

    面板 flair：model 前缀 🍟、elapsed 前缀 ⏱️（工具执行/会话耗时）、💭/🔧 为面板
    专属计数（推理轮数/工具步数）；context 按 context_display_mode 渲染；
    其余共享字段（speed/cache/tokens/status）原样回落 footer 渲染器。
    """
    if name == "model":
        v = (data.get("model") or "").strip()
        if v:
            v = f"🍟 {_display_model(v)}"
            return v, v
        return None, None
    if name == "process_counts":
        return (_T["process_counts_compact"][0].format(reasoning_count, tool_count),
                _T["process_counts_compact"][1].format(reasoning_count, tool_count))
    if name == "reasoning":
        return (_T["reasoning_count"][0].format(reasoning_count),
                _T["reasoning_count"][1].format(reasoning_count))
    if name == "tools":
        return (_T["tool_count"][0].format(tool_count), _T["tool_count"][1].format(tool_count))
    if name == "elapsed":
        if panel_elapsed_ms > 0:
            v = f"⏱️ {_format_elapsed(panel_elapsed_ms)}"
            return v, v
        return None, None
    if name == "context":
        used = data.get("context_used", 0) or 0
        max_c = data.get("context_max", 0) or 0
        if show_context and used > 0 and max_c > 0:
            if context_mode == "text":
                v = _context_text(used, max_c)
            elif context_mode in ("bar", "block"):
                # block 样式桌面/移动端显示不一致，回落 bar
                v = f"[{_context_progress_bar(used, max_c)}] {_context_pct(used, max_c)}"
            else:  # text_bar / block_text
                v = _context_progress_with_text(used, max_c)
            return v, v
        return None, None
    return _render_footer_field(name, data, False, False, False)


_PANEL_DEFAULT_FIELDS = ("model", "elapsed", "speed", "context")


def _build_panel_header(
    fields: list[str] | None,
    data: dict,
    *,
    reasoning_count: int,
    tool_count: int,
    panel_elapsed_ms: int | float,
    show_context: bool,
    context_mode: str,
    counts_in_header: bool = True,
) -> tuple[str, str]:
    """组装统一面板 header 文本，返回 (en, zh)。无效/缺数据字段自动隐藏.

    ``counts_in_header=False`` 供封卡用：思考轮数与工具步数改由详情框首行呈现
    （``_completed_counts_element``），header 只留调用方点名的其余字段。
    """
    if not fields:
        fields = list(_PANEL_DEFAULT_FIELDS)
    trailing = ("reasoning", "tools") if counts_in_header else ()
    fields = list(dict.fromkeys(
        "process_counts" if name in ("reasoning", "tools") else name
        for name in [*fields, *trailing]
    ))
    if not counts_in_header:
        fields = [name for name in fields if name != "process_counts"]
    en_parts: list[str] = []
    zh_parts: list[str] = []
    for name in fields:
        en, zh = _render_panel_field(
            name,
            data,
            reasoning_count=reasoning_count,
            tool_count=tool_count,
            panel_elapsed_ms=panel_elapsed_ms,
            show_context=show_context,
            context_mode=context_mode,
        )
        if en:
            en_parts.append(en)
            zh_parts.append(zh or en)
    # 面板出现的前提是有推理/工具内容，默认字段下 header 必非空；
    # 自定义组合全部隐藏时回落 🍟 占位，避免空标题栏
    return " · ".join(en_parts) or "🍟", " · ".join(zh_parts) or "🍟"


@styled_builder
def _build_footer_elements(
    footer_data: dict | None,
    is_error: bool = False,
    is_aborted: bool = False,
    fields: list[list[str]] | None = None,
    show_label: bool = False,
    text_size: str = "notation",
    *, config: Config | None = None,
) -> list[dict]:
    if fields is None:
        fields = [["status", "elapsed", "speed", "cache", "context", "model"]]

    data = footer_data or {}
    en_lines: list[str] = []
    zh_lines: list[str] = []
    for row in fields:
        en_parts: list[str] = []
        zh_parts: list[str] = []
        for field in row:
            en, zh = _render_footer_field(field, data, is_error, is_aborted, show_label)
            if en:
                en_parts.append(en)
                if zh:
                    zh_parts.append(zh)
        if en_parts:
            en_lines.append(option("footer", "separator", " · ").join(en_parts))
            zh_lines.append(option("footer", "separator", " · ").join(zh_parts))

    if not en_lines:
        return []

    en_content = "\n".join(en_lines)
    zh_content = "\n".join(zh_lines)

    element = text_element({
            "tag": "markdown",
            "content": en_content,
            "i18n_content": _i18n(en_content, zh_content),
            "text_size": text_size,
        }, "footer")
    if is_error:
        color = option("footer", "error_color", "red")
        for key in ("content", "i18n_content"):
            if key == "content":
                element[key] = _error_footer_content(element[key], color)
            else:
                element[key] = {lang: _error_footer_content(text, color)
                                for lang, text in element[key].items()}
    return [element]


def _error_footer_content(text: str, color: str) -> str:
    lines = []
    for line in text.split("\n"):
        outer = re.fullmatch(r"<font color=['\"][^'\"]+['\"]>(.*)</font>", line)
        lines.append(outer[1] if outer else line)
    content = "\n".join(lines)
    return f"<font color='{color}'>{content}</font>"


def _render_footer_field(
    name: str,
    data: dict,
    is_error: bool,
    is_aborted: bool,
    show_label: bool,
) -> tuple[str | None, str | None]:
    if name == "status":
        if is_error:
            return _T["status_error"]
        if is_aborted:
            return _T["status_stopped"]
        return _T["status_completed"]

    if name == "elapsed":
        duration = data.get("duration", 0)
        if isinstance(duration, (int, float)) and duration > 0:
            val = _format_elapsed(duration * 1000)
            if show_label:
                return _T["elapsed"][0].format(val), _T["elapsed"][1].format(val)
            return val, val
        return None, None

    if name == "model":
        v = data.get("model") or None
        if v:
            v = _display_model(v)
        return v, v

    if name == "tokens":
        input_t = data.get("input_tokens", 0) or 0
        output_t = data.get("output_tokens", 0) or 0
        if input_t or output_t:
            v = f"↑ {_compact(input_t)} ↓ {_compact(output_t)}"
            return v, v
        return None, None

    if name == "speed":
        tps = data.get("tokens_per_sec", 0) or 0
        if tps > 0:
            v = f"{tps:.0f} tok/s"
            return v, v
        return None, None

    if name == "cache":
        # 口径同 Hermes（cli_status_bar_mixin）：hit = cache_read / prompt_tokens，
        # prompt（input_tokens）已含缓存部分；零读取时隐藏（无数据 ≠ 0%）。
        cache_read = data.get("cache_read_tokens", 0) or 0
        input_t = data.get("input_tokens", 0) or 0
        if cache_read > 0 and input_t > 0:
            pct = min(round(cache_read / input_t * 100), 100)
            return _T["cache_hit"][0].format(pct), _T["cache_hit"][1].format(pct)
        return None, None

    if name == "context":
        used = data.get("context_used", 0) or 0
        max_c = data.get("context_max", 0) or 0
        if max_c:
            pct = int(used / max_c * 100)
            val = f"{_compact(used)}/{_capacity(max_c)} ({pct}%)"
            if show_label:
                return _T["context"][0].format(val), _T["context"][1].format(val)
            return val, val
        return None, None

    return None, None


def _compact(n: int) -> str:
    if n >= 1_000_000:
        m = n / 1_000_000
        return f"{int(m)}M" if m >= 100 else f"{m:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _capacity(n: int, *, upper: bool = True) -> str:
    """模型上下文上限按整数单位显示（512K / 1M），容量值不带小数。

    用量会随轮次变化，保留一位小数有意义；上限是固定容量，小数只是噪音。
    面板文本用小写单位（512k），footer 用大写（512K），靠 ``upper`` 区分。
    """
    unit_k, unit_m = ("K", "M") if upper else ("k", "m")
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}{unit_m}"
    if n >= 1_000:
        return f"{n / 1_000:.0f}{unit_k}"
    return str(n)


def _format_elapsed(ms: float) -> str:
    seconds = ms / 1000
    return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}m {int(seconds % 60)}s"


_CONTEXT_BAR_STEPS = ("█", "▓", "▒", "░")  # 实 → 半实 → 半空 → 空，渐变阴影


def _context_progress_bar(used: int, total: int, width: int = 8) -> str:
    """生成渐变阴影进度条，格式: ███▓▒░░░（实心到空的密度渐变）"""
    if total <= 0:
        return ""
    pct = min(used / total * 100, 100)
    n = pct / 100 * width  # 浮点进度位置（0..width）
    cells: list[str] = []
    for i in range(width):
        pos = i + 0.5  # 每格中心
        if pos <= n:
            # 完全填满的格子：除了最靠近边界的一格，其余都是实心
            if n - pos >= 0.5:
                cells.append("█")
            elif n - pos >= 0.25:
                cells.append("▓")
            else:
                cells.append("▒")
        elif pos - 1 <= n:
            # 边界格：按剩余进度选密度
            frac = n - (pos - 1)  # 0..1
            if frac > 0.66:
                cells.append("▓")
            elif frac > 0.33:
                cells.append("▒")
            else:
                cells.append("░")
        else:
            cells.append("░")
    return "".join(cells)


def _context_progress_block(used: int, total: int, width: int = 10) -> str:
    """[已废弃] block 样式在桌面端/移动端显示不一致，不再使用."""
    return _context_progress_bar(used, total, width)


def _context_text(used: int, total: int) -> str:
    """生成上下文纯文本，格式: 55.6k/512k (5%)  —— 上限取整，用量保留一位小数"""
    if total <= 0:
        return ""
    pct = min(used / total * 100, 100)
    total_str = _capacity(total, upper=False)
    if total >= 1_000_000:
        # total 用 m，used 根据大小用 k 或 m
        if used < 1_000_000:
            used_str = f"{used / 1_000:.1f}k"
        else:
            used_str = f"{used / 1_000_000:.1f}m"
        return f"{used_str}/{total_str} ({pct:.0f}%)"
    if total >= 1_000:
        return f"{used / 1_000:.1f}k/{total_str} ({pct:.0f}%)"
    return f"{used}/{total_str} ({pct:.0f}%)"


def _context_pct(used: int, total: int) -> str:
    """生成上下文使用百分比，格式: 35%"""
    if total <= 0:
        return ""
    pct = min(used / total * 100, 100)
    return f"{pct:.0f}%"


def _context_progress_with_text(used: int, total: int, width: int = 8) -> str:
    """生成文本+渐变进度条，格式: 55.6k/512k [███▓▒░░░] 5%（上限取整）"""
    if total <= 0:
        return ""
    pct = min(used / total * 100, 100)
    bar = _context_progress_bar(used, total, width)
    total_str = _capacity(total, upper=False)
    if total >= 1_000_000:
        if used < 1_000_000:
            used_str = f"{used / 1_000:.1f}k"
        else:
            used_str = f"{used / 1_000_000:.1f}m"
        return f"{used_str}/{total_str} [{bar}] {pct:.0f}%"
    if total >= 1_000:
        return f"{used / 1_000:.1f}k/{total_str} [{bar}] {pct:.0f}%"
    return f"{used}/{total_str} [{bar}] {pct:.0f}%"


@styled_builder
def build_streaming_tool_use_pending_panel(*, expanded: bool = True, config: Config | None = None) -> dict[str, Any]:
    panel = _collapsible_panel(
        expanded=expanded, title_el=_process_title(element_id=f"{TOOL_PANEL_ELEMENT_ID}_count", tool_mode=True),
        elements=[],
    )
    panel["element_id"] = TOOL_PANEL_ELEMENT_ID
    if not (config or current_config()).process_border:
        panel.pop("border")
    return panel


@styled_builder
def build_streaming_process_pending_panel(*, expanded: bool = True, config: Config | None = None) -> dict[str, Any]:
    """Native bottom header controls the complete reasoning timeline."""
    panel = _collapsible_panel(
        expanded=expanded,
        title_el=_process_title(element_id=f"{PROCESS_PANEL_ELEMENT_ID}_count"),
        elements=[{"tag": "markdown", "content": " ", "element_id": PROCESS_ANCHOR_ELEMENT_ID,
                   "text_size": "notation"}],
    )
    panel["element_id"] = PROCESS_PANEL_ELEMENT_ID
    if not (config or current_config()).process_border:
        panel.pop("border")
    return panel


@styled_builder
def build_streaming_card_v2(
    *,
    tool_steps: list[ToolDisplayStep] | None = None,
    elapsed_ms: float = 0,
    show_tool_use: bool = True,
    show_reasoning: bool = False,
    show_streaming_element: bool = True,
    header_enabled: bool = False,
    text_size: str = "normal_v2",
    width_mode: str = "default",
    notice: str | None = None,
    process_expanded: bool = True,
    config: Config | None = None,
) -> dict[str, Any]:
    """CardKit 2.0 流式占位卡片 — 工具面板合并到底部.

    ``process_expanded`` 决定过程框建卡时是否展开，取自配置 ``streaming.process_expanded``。
    """
    config = config or current_config()
    elements: list[dict] = []

    if show_streaming_element:
        elements.append(_streaming_element(text_size=text_size))

    if show_reasoning:
        elements.append(build_streaming_process_pending_panel(expanded=process_expanded, config=config))

    # 工具面板放在底部（answer之后，loading之前）
    if show_tool_use:
        if tool_steps:
            panel = _build_tool_panel(tool_steps, elapsed_ms, running=True, expanded=False, config=config)
            panel["header"] = _panel_header(_process_title(
                0, len(tool_steps), element_id=f"{TOOL_PANEL_ELEMENT_ID}_count", tool_mode=True,
            ))
            elements.append(panel)
        else:
            elements.append(build_streaming_tool_use_pending_panel(expanded=process_expanded, config=config))

    if notice:
        # 通知区：流式态只显示最新一条（Hermes 原文），落卡片底部（loading 之前），
        # 不和工具输出混在一起（#43 反馈）；完成态按提示种类分区呈现。
        # 用流式元素承载（固定 element_id + 正文字号）：首段正文到达时在同一个元素上
        # 原地覆盖成正文，不在旁边新增一段（#issue 提示元素未被正文覆盖）。
        elements.append(
            build_streaming_notice_element(
                notice, element_id=_NOTICE_ELEMENT_ID, text_size=text_size
            )
        )

    elements.append(_loading_element())

    body = _build_body(elements)
    body["elements"].append(_loading_cursor_element())

    card = {
        "schema": "2.0",
        "config": {
            "width_mode": width_mode,
            "streaming_mode": True,
            "style": _body_style(config),
            "locales": _LOCALES,
            "summary": {
                "content": _T["processing"][0],
                "i18n_content": _t("processing"),
            },
        },
        "body": body,
    }
    if header_enabled:
        card["header"] = _build_header("streaming", config=config)
    return card


@styled_builder
def build_complete_card(
    *,
    segments: list[Segment],
    all_tool_steps: list[ToolDisplayStep],
    process_messages: list[str] | None = None,
    footer_data: dict | None = None,
    is_error: bool = False,
    is_aborted: bool = False,
    is_continuation: bool = False,
    footer_fields: list[list[str]] | None = None,
    footer_show_label: bool = True,
    footer_enabled: bool = True,
    footer_text_size: str = "notation",
    panel_fields: list[str] | None = None,
    panel_expanded: bool = False,
    panel_text_size: str = "notation",
    header_enabled: bool = False,
    body_text_size: str = "normal_v2",
    show_tool_use: bool = True,
    show_reasoning: bool = True,
    reasoning_count: int | None = None,
    tool_count: int | None = None,
    total_tool_elapsed_ms: float | None = None,
    width_mode: str = "default",
    notices: list[str] | None = None,
    progress_notices: list[str] | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """完成态流式卡片 — 推理+工具合并成底部统一面板，答案在上面.

    统一面板内部按 segments 的**真实发生顺序**交错渲染：
    💭 思考1 → 🔧 工具组1 → 💭 思考2 → 🔧 工具组2 …（对齐工作流时间线）。

    通知区分两个展示位（#43 反馈）：``notices``（后台回执等完成后通知）在正文
    顶部；``progress_notices``（忙碌回执 / 重定向等本轮进行中的提示）在卡片
    底部（footer 之前），不与工具输出混排。元素 id 跨区连续派生，保证唯一。
    """
    config = config or current_config()
    elements: list[dict] = []
    has_answer = False
    # 收集所有 reasoning rounds + tool steps，合并成一个底部统一面板
    reasoning_rounds: list[dict] = []
    tool_steps_total: list[ToolDisplayStep] = []
    tool_elapsed_ms: int | float = 0
    # 过程时间线：按 segments 到达顺序保序记录 reasoning / tool 组，
    # 终态据此交错渲染（不再「先所有思考、再所有工具」两堆堆叠）。
    timeline: list[dict] = []

    if reasoning_count is None:
        reasoning_count = sum(s.type == SegmentType.REASONING and bool(s.text) for s in segments)
    if tool_count is None:
        tool_count = len({i for s in segments if s.type == SegmentType.TOOL
                          for i in range(s.tool_offset, min(s.tool_end_offset or len(all_tool_steps),
                                                            len(all_tool_steps)))})
    for seg in segments:
        if seg.type == SegmentType.REASONING:
            if show_reasoning and seg.text:
                # 尾部窗口钳制（#52）：与流式 reasoning 同一预算。完成态整卡重建把
                # 全文再发一遍，28KB 级 reasoning 叠加正文会越过 ~30KB 卡片 JSON 上限；
                # 钳制后完成态与流式期观感也保持一致（同一份头部摘要+尾部结论）。
                round_entry = {
                    "text": clamp_utf8(
                        seg.text, max_bytes=_REASONING_MAX_BYTES, preserve_tail=True,
                    ),
                    "elapsed_ms": seg.elapsed_ms,
                    "text_el_id": seg.text_el_id,  # 复用流式阶段的 text element id，避免完成态 Duplicate ID
                }
                reasoning_rounds.append(round_entry)
                timeline.append({"kind": "reasoning", "round": round_entry})
        elif seg.type == SegmentType.TOOL:
            if not show_tool_use:
                continue
            start = seg.tool_offset
            end = seg.tool_end_offset if seg.tool_end_offset else len(all_tool_steps)
            steps = all_tool_steps[start:end]
            if steps:
                tool_steps_total.extend(steps)
                tool_elapsed_ms += seg.elapsed_ms or 0
                timeline.append({
                    "kind": "tools",
                    "steps": steps,
                    "elapsed_ms": seg.elapsed_ms or 0,
                })
        elif seg.type == SegmentType.ANSWER and seg.text:
            has_answer = True
            _append_content_elements(elements, seg.text, text_size=body_text_size)

    if total_tool_elapsed_ms is None:
        total_tool_elapsed_ms = sum(max(0, step.get("elapsed_ms", 0) or 0) for step in all_tool_steps)

    # Completed details preserve visible progress text in arrival order.
    detail_messages = list(process_messages or [])
    # 停止卡不建框（用户口径）：停止卡会被新卡接手，旧卡上的计数与过程没人看。
    # 拆卡后的交接卡同理不建框：本段正文全部交给新卡，旧卡只留一句「正文见后续卡片」。
    # 出错卡与正常完成卡照旧建框：框内只放中途消息，没有则一句占位。
    if not is_aborted and not is_continuation:
        border_color = "red" if is_error else "green"
        # 首行是本轮计数（思考轮数 · 工具步数），它本身就是框内内容，
        # 因此不再另外补「本轮没有中途消息」这句占位。
        unified_children: list[dict] = [_completed_counts_element(reasoning_count, tool_count)]
        for message in detail_messages:
            _append_content_elements(unified_children, message, text_size=body_text_size)
        # 框头耗时口径固定为整轮（用户口径）：不随工具明细开关在「工具累计」和
        # 「整轮」之间漂移；拿不到整轮时长时退回工具累计。
        panel_elapsed_ms = 0
        _p_duration = (footer_data or {}).get("duration")
        if isinstance(_p_duration, (int, float)) and _p_duration > 0:
            panel_elapsed_ms = _p_duration * 1000
        elif tool_elapsed_ms:
            panel_elapsed_ms = tool_elapsed_ms
        header_en, header_zh = _build_panel_header(
            panel_fields,
            footer_data or {},
            reasoning_count=reasoning_count,
            tool_count=tool_count,
            panel_elapsed_ms=panel_elapsed_ms,
            show_context=config.show_context,
            context_mode=config.context_display_mode,
            counts_in_header=False,
        )
        unified_panel = {
            "tag": "collapsible_panel",
            # 固定 id：封卡撞 300305 时按 id 精确剥离整个面板重发（#53 修法 2）
            "element_id": UNIFIED_PANEL_ELEMENT_ID,
            "expanded": panel_expanded,
            # 折叠与展开共用比正文深一号的底色：header 未单独指定时继承它；
            # 边框同色，出错仍保留红色。
            "background_color": BODY_SURFACE_DEEP_COLOR,
            "header": {
                **_panel_header({
                    "tag": "plain_text", "content": header_en,
                    "i18n_content": _i18n(header_en, header_zh),
                }, position="top", arrow=False),
                "text_color": option("process_title", "text_color", "grey"),
                "text_size": panel_text_size,
            },
            "border": {
                "color": border_color if is_error else BODY_SURFACE_DEEP_COLOR,
                "corner_radius": "5px",
            },
            "elements": unified_children,
        }
        unified_panel = panel_element(
            unified_panel, completed=True, status="error" if is_error else "completed",
        )
        elements.append(unified_panel)

    if not has_answer:
        # 有思考段却没有正文 = 回合在正文写出前被截断（服务重启、上游断流），
        # 落 Done. 会被读成「回复就一个 Done」；只有真的无过程可陈时才留 Done.。
        cut_off = bool(reasoning_rounds) or bool(tool_steps_total)
        key = "continued" if is_continuation else ("interrupted_no_answer" if cut_off else "done")
        en, zh = _T[key]
        elements.append(text_element({
            "tag": "markdown", "content": en, "i18n_content": _i18n(en, zh),
            "text_size": body_text_size,
        }, "body"))

    # 进行中的提示（忙碌回执 / 重定向）落底部：正文之后、footer 之前（#43 反馈）。
    # 元素 id 接在顶部提示序号之后派生，两个区不撞 id。
    elements.extend(build_notice_elements(progress_notices, offset=len(notices or [])))

    if footer_enabled:
        elements.extend(
            _build_footer_elements(
                footer_data,
                is_error,
                is_aborted,
                fields=footer_fields,
                show_label=footer_show_label,
                text_size=footer_text_size,
            )
        )

    summary_text = ""
    for seg in reversed(segments):
        if seg.text and (seg.type == SegmentType.ANSWER or (show_reasoning and seg.type == SegmentType.REASONING)):
            summary_text = seg.text
            break
    summary = summary_text[:120].replace("\n", " ").replace("```", "").strip()

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "width_mode": width_mode,
            "wide_screen_mode": True,
            "update_multi": True,
            "style": _body_style(config),
            "locales": _LOCALES,
        },
    }
    if summary:
        card["config"]["summary"] = {"content": summary}
    # 通知区放在正文顶部：Hermes 原文（忙碌回执 / 重定向 / 后台回执）不另发纯文本（#43）。
    # 每条提示占一个元素（id 按序号派生），元素数计入 session.element_count。
    card["body"] = _build_body(build_notice_elements(notices) + elements)
    if header_enabled:
        header_status = "error" if is_error else "stopped" if is_aborted else "completed"
        card["header"] = _build_header(header_status, config=config)
    return card


def _format_run_time(run_time: str) -> str:
    """将 ISO 时间戳格式化为可读日期时间，失败则原样返回."""
    if not run_time:
        return ""
    try:
        dt = datetime.fromisoformat(run_time)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return run_time


@styled_builder
def build_cron_card(
    content: str, *, task_name: str = "", run_time: str = "", config: Config | None = None
) -> dict[str, Any]:
    """Cron 推送卡片复用正文主题，可选 header 与原生表格."""
    cfg = config or current_config()
    elements: list[dict] = []
    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "locales": _LOCALES, "style": _body_style(cfg),
                   "width_mode": cfg.width_mode},
        "body": _build_body(elements),
    }
    header_parts = [p for p in (task_name, _format_run_time(run_time)) if p]
    if header_parts and cfg.header_enabled:
        image_key = cfg.header_icons.get("cron")
        header = {
            "title": {"tag": "lark_md", "content": ("" if image_key else ":Alarm: ") + " · ".join(header_parts)},
        }
        if image_key:
            header["icon"] = {"tag": "custom_icon", "img_key": image_key}
        card["header"] = header_element(header)
    template = cfg.header_template
    if "header" in card and template != "default":
        card["header"]["template"] = template
    if not content.strip():
        return card
    summary = content[:120].replace("\n", " ").replace("```", "").strip()
    if summary:
        card["config"]["summary"] = {"content": summary}
    _append_content_elements(elements, content, text_size=cfg.body_text_size)
    card["body"] = _build_body(elements)
    return card


@styled_builder
def build_background_card(
    preview: str, content: str, *, config: Config | None = None, has_reference: bool = False,
) -> dict[str, Any]:
    """Background notices use a title for standalone delivery and the original body theme."""
    cfg = config or current_config()
    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "locales": _LOCALES,
                   "width_mode": cfg.width_mode, "style": _body_style(cfg)},
        "body": {"elements": []},
    }
    body = content if content.strip() else "(No response generated)"
    summary = body[:120].replace("\n", " ").replace("```", "").strip()
    if summary:
        card["config"]["summary"] = {"content": summary}
    _append_content_elements(card["body"]["elements"], body, text_size=cfg.body_text_size)
    card["body"] = _build_body(card["body"]["elements"])
    if not has_reference and cfg.header_enabled:
        card["header"] = build_notice_header(config=cfg, title_key="notice_background")
    return card


def count_elements_recursive(value: Any) -> int:
    """递归统计卡片树里的元素节点数（#56 诊断）.

    飞书 300305 校验的是全树递归节点总数，``len(body.elements)`` 只看顶层会严重
    低估——统一面板里嵌的工具组/思考面板每个都带一串子元素。含 ``tag`` 键的
    dict 记一个元素，容器内的嵌套元素全部计入。
    """
    if isinstance(value, dict):
        count = 1 if "tag" in value else 0
        for child in value.values():
            count += count_elements_recursive(child)
        return count
    if isinstance(value, list):
        return sum(count_elements_recursive(child) for child in value)
    return 0


def strip_unified_panel(card: dict[str, Any]) -> int:
    """去掉完成态卡片的统一面板（思考 + 工具过程），就地修改，返回剥离的元素数（#53）.

    封卡撞 300305 时的应急重发载荷：面板子元素在流式期已提交过一次，整卡重建
    会被服务端按「已有 + 本次」重复计数，是元素超限的最大来源。剥离后正文与
    状态优先入卡；仍超限才走 build_overflow_fallback_card 整卡降级。返回 0 表示
    卡上没有统一面板。
    """
    body = card.get("body")
    root = body.get("elements") if isinstance(body, dict) else None
    if not isinstance(root, list):
        return 0
    groups = [root]
    for element in root:
        if isinstance(element, dict) and str(element.get("element_id", "")).startswith(BODY_SURFACE_ELEMENT_ID):
            groups.append(element["columns"][0]["elements"])
    removed = 0
    for elements in groups:
        kept = [el for el in elements
                if not (isinstance(el, dict) and el.get("element_id") == UNIFIED_PANEL_ELEMENT_ID)]
        removed += len(elements) - len(kept)
        elements[:] = kept
    return removed


@styled_builder
def build_overflow_fallback_card(*, config: Config | None = None) -> dict[str, Any]:
    """封卡失败（300305 元素超限）的降级卡片：一句提示，正文由纯文本兜底投递（#22）."""
    cfg = config or current_config()
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "locales": _LOCALES, "style": _body_style(cfg),
                   "width_mode": cfg.width_mode},
        "header": _build_header("error", config=config),
        "body": _build_body([
            text_element({
                "tag": "markdown",
                "content": _T["card_overflow_fallback"][0],
                "i18n_content": _t("card_overflow_fallback"),
                "text_size": cfg.body_text_size,
            }, "body")
        ]),
    }
