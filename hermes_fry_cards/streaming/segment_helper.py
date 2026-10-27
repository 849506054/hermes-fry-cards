"""Segment 对应的 CardKit action 构造与容量估算."""

from __future__ import annotations

from typing import Any

from ..cardkit.builder import (
    _LOADING_ELEMENT_ID,
    MAX_TOOL_PANEL_STEPS,
    _build_reasoning_panel,
    _build_tool_panel,
    _build_tool_step_elements,
    _format_elapsed,
    _streaming_element,
    count_card_elements,
)
from ..cardkit.i18n import _T, _i18n
from .segments import Segment, SegmentType
from .tooluse import ToolDisplayStep

ELEMENT_THRESHOLD = 180  # 飞书硬上限 200，预留 20（18 给波动 + 2 footer 预留）
FOOTER_RESERVE = 2  # footer 元素预留（hr + markdown）


def estimate_answer_elements(text: str) -> int:
    """估算 answer segment 当前文本在服务端占用的元素数（保守取两条路径的大者）。

    服务端真实元素来自两条路径：
    1. 流式态：answer 是单个 markdown 元素，但 markdown 表格在服务端渲染时
       每个单元格（含表头）展开为独立元素并计入 200 硬上限（#5/#22 实测证实），
       markdown 图片每张也占 1 个 img 元素；
    2. 完成态：build_complete_card 经 _append_content_elements 重建——表格片段
       出原生 table 元素（1 元素/表，超限表格已被 _downgrade_tables 降级为字段
       列表并入文本），文本片段按 _MAX_CHUNK_CHARS 切块为多个 markdown 元素。

    取 max(流式, 完成态) 作为保守上界。流式阶段 answer 文本持续增长，此估算必须
    每次 flush 重算并同步差值，否则 element_count 严重低估 → 撞 300305 才被动拆卡。
    """
    if not text or not text.strip():
        return 1
    from ..cardkit.markdown import (
        _ANSWER_MAX_BYTES,
        _downgrade_tables,
        _split_long_text,
        optimize_markdown_style,
        parse_table_block,
        split_markdown_tables,
    )
    # 必须与 _append_content_elements 的渲染链完全一致：
    #   optimize_markdown_style → _downgrade_tables → clamp_utf8 → split_markdown_tables
    content = _downgrade_tables(optimize_markdown_style(text))
    # clamp_utf8 会把超长回答截断到 _ANSWER_MAX_BYTES，截断后行数减少、
    # 表格可能被腰斩，两条路径都应按截断后的内容算，否则高估。
    if len(content.encode("utf-8")) > _ANSWER_MAX_BYTES:
        content = content[:_ANSWER_MAX_BYTES]

    streaming = 1  # 流式态 answer 的 markdown 元素本身
    complete = 0
    for kind, part in split_markdown_tables(content):
        if kind == "table":
            parsed = parse_table_block(part)
            if parsed:
                header, rows = parsed
                # 服务端把表头行也渲染为单元格：(1+数据行数)*列数 + 表格容器开销
                streaming += (1 + max(1, len(rows))) * len(header) + 1
                complete += 1  # 原生 table 元素只占 1 个元素位
                continue
        for chunk in _split_long_text(part):
            if chunk.strip():
                complete += 1
            streaming += chunk.count("![")  # markdown 图片 → img 元素
    return max(1, streaming, complete)


def estimate_segment_elements(seg: Segment, all_steps: list[ToolDisplayStep]) -> int:
    """估算单个 segment 新增的卡片元素数."""
    if seg.type == SegmentType.REASONING:
        return 4  # collapsible_panel + plain_text + standard_icon + markdown
    if seg.type == SegmentType.ANSWER:
        return estimate_answer_elements(seg.text)
    if seg.type == SegmentType.TOOL:
        return estimate_tool_elements(
            seg.tool_offset,
            tool_segment_end(seg, all_steps),
            all_steps,
        )
    return 0


def tool_segment_end(seg: Segment, all_steps: list[ToolDisplayStep]) -> int:
    return seg.tool_end_offset if seg.tool_end_offset else len(all_steps)


def estimate_tool_elements(start: int, end: int, all_steps: list[ToolDisplayStep]) -> int:
    """估算 tool panel 在 [start, end) step 区间内的元素数。

    渲染端只保留最近 ``MAX_TOOL_PANEL_STEPS`` 步（#46，更早的折叠成一行摘要），
    估算按同一上限截取，否则长任务轮次里估算随步数无界增长，会过早触发拆卡。

    单步成本直接数 ``_build_tool_step_elements`` 的真实产物，不再按常数折算：
    常数（5/7）与实际渲染（3）对不上时，账面余额是假的，剩余预算判据跟着失真。
    """
    steps = all_steps[start:end]
    count = 3  # panel/header 基础元素
    if len(steps) > MAX_TOOL_PANEL_STEPS:
        count += 1  # 「…另有 N 步」摘要行
        steps = steps[-MAX_TOOL_PANEL_STEPS:]
    for step in steps:
        count += count_card_elements(_build_tool_step_elements(step))
    return count


def panel_steps_start(segments: list[Segment], split_index: int, default: int) -> int:
    """本卡共享工具面板的渲染起点：本卡首个工具段（拆卡后不重发旧卡步骤）.

    ``segments[split_index:]`` 是本卡承接的 segment，按顺序排列，首个 TOOL 段
    的 ``tool_offset`` 即本卡面板起点。不能取最小值：同一张卡上多个工具段合并为
    一个面板，后面打开的段 ``tool_offset`` 可能小于前面已入账段的 ``tool_end_offset``
    （终结前记录的是 step 数，减 1 后偏移有差），取最小会让面板估算回退、增量失真。
    若本卡尚无工具段，回落到调用方的 ``default``。
    """
    for seg in segments[split_index:]:
        if seg.type == SegmentType.TOOL:
            return seg.tool_offset
    return default


def find_tool_split_offset(
    *,
    base_count: int,
    seg: Segment,
    all_steps: list[ToolDisplayStep],
    panel_start: int | None = None,
) -> int | None:
    """寻找 tool step 拆分点，让当前卡保留尽可能多的 steps.

    ``panel_start`` 为本卡面板渲染起点（默认本段起点）。共享面板保留
    ``[panel_start, split_offset)``，估算必须按这些步骤算。始终至少保留 1 个
    step：否则拆出来的首段是空的，拆卡也无法降下来。
    """
    start = seg.tool_offset
    end = tool_segment_end(seg, all_steps)
    if end - start <= 1:
        return None
    panel_start = start if panel_start is None else panel_start
    for split_offset in range(end - 1, start, -1):
        estimate = estimate_tool_elements(panel_start, split_offset, all_steps)
        if base_count + estimate + FOOTER_RESERVE <= ELEMENT_THRESHOLD:
            return split_offset
    return None


def build_add_segment_action(
    seg: Segment, all_steps: list[ToolDisplayStep], *, text_size: str = "normal_v2",
) -> dict[str, Any]:
    """构造新增 segment 元素的 batch action."""
    if seg.type == SegmentType.REASONING:
        element = _build_reasoning_panel(
            " ",
            seg.elapsed_ms,
            expanded=True,
            element_id=seg.el_id,
            text_element_id=seg.text_el_id,
        )
    elif seg.type == SegmentType.ANSWER:
        element = _streaming_element(element_id=seg.el_id, text_size=text_size)
    elif seg.type == SegmentType.TOOL:
        start = seg.tool_offset
        end = seg.tool_end_offset if seg.tool_end_offset else len(all_steps)
        element = _build_tool_panel(all_steps[start:end], element_id=seg.el_id, running=True)
    else:
        raise ValueError(f"unsupported segment type: {seg.type}")

    return {
        "action": "add_elements",
        "params": {
            "type": "insert_before",
            "target_element_id": _LOADING_ELEMENT_ID,
            "elements": [element],
        },
    }


def build_reasoning_finalized_action(seg: Segment) -> dict[str, Any]:
    """构造 reasoning header 耗时终结 action."""
    elapsed = _format_elapsed(seg.elapsed_ms)
    en_label = _T["thought_for"][0].format(elapsed)
    zh_label = _T["thought_for"][1].format(elapsed)
    return {
        "action": "partial_update_element",
        "params": {
            "element_id": seg.el_id,
            "partial_element": {
                "expanded": False,
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"💭 {en_label}",
                        "i18n_content": _i18n(f"💭 {en_label}", f"💭 {zh_label}"),
                        "text_color": "grey",
                        "text_size": "notation",
                    },
                },
            },
        },
    }


def build_tool_update_action(
    *,
    element_id: str,
    steps: list[ToolDisplayStep],
    running: bool = True,
) -> dict[str, Any]:
    """构造 tool panel 局部更新 action."""
    panel = _build_tool_panel(steps, running=running)
    return {
        "action": "partial_update_element",
        "params": {
            "element_id": element_id,
            "partial_element": {
                "elements": panel["elements"],
                "header": panel["header"],
            },
        },
    }
