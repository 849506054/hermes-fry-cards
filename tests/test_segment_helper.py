"""streaming.segment_helper 测试 — CardKit 元素容量估算与 tool 拆分点."""

from __future__ import annotations

from hermes_fry_cards.cardkit.builder import _build_tool_step_elements, count_card_elements
from hermes_fry_cards.streaming.segment_helper import (
    ELEMENT_THRESHOLD,
    FOOTER_RESERVE,
    estimate_answer_elements,
    estimate_segment_elements,
    estimate_tool_elements,
    find_tool_split_offset,
    tool_segment_end,
)
from hermes_fry_cards.streaming.segments import Segment, SegmentType
from hermes_fry_cards.streaming.tooluse import ToolDisplayStep


def _step(*, detail: str = "", result: bool = False, error: bool = False) -> ToolDisplayStep:
    return {
        "name": "read",
        "title": "Read",
        "status": "running",
        "detail": detail,
        "output": "",
        "error": "",
        "icon": "tool",
        "elapsed_ms": 0,
        "result_block": {"language": "text", "content": "ok", "fenced": "ok"} if result else None,
        "error_block": {"language": "text", "content": "boom", "fenced": "boom"} if error else None,
    }


def test_estimate_segment_elements_for_basic_types() -> None:
    reasoning = Segment(SegmentType.REASONING, "reasoning")
    answer = Segment(SegmentType.ANSWER, "answer")
    tool = Segment(SegmentType.TOOL, "tool")
    steps = [_step()]

    assert estimate_segment_elements(reasoning, steps) == 4
    assert estimate_segment_elements(answer, steps) == 1
    # 面板 3 格基座 + 该步真实渲染产物（2026-10-27 起按 _build_tool_step_elements 数，不按常数折算）
    assert estimate_segment_elements(tool, steps) == 4


def test_estimate_answer_elements_grows_with_tables_and_length() -> None:
    """answer 段估算必须随服务端膨胀源增长（表格单元格 / 长文切块）。

    回归：旧实现恒返回 1，导致 element_count 严重低估 → 撞 300305 被动拆卡。
    """
    # 空 / 纯文本：单元素
    assert estimate_answer_elements("") == 1
    assert estimate_answer_elements("   ") == 1
    assert estimate_answer_elements("hello world") == 1

    # 表格：每个数据单元格 + 表格容器都要计入（服务端展开为独立元素）
    table = "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 | 6 |"
    with_table = estimate_answer_elements(table)
    assert with_table > 1, "表格必须推高估算"
    assert estimate_answer_elements(table + "\n\n" + table) > with_table, "表格越多估算越大"

    # 长文：完成态按 _MAX_CHUNK_CHARS 切块，块数计入
    long_text = "x" * 3000
    assert estimate_answer_elements(long_text) >= 2, "超长文本必须按块数估算"
    assert estimate_answer_elements("x" * 6000) > estimate_answer_elements(long_text)

    # 代码块内的伪表格不算表格（服务端不展开）
    fenced = "```\n| A | B |\n|---|---|\n| 1 | 2 |\n```"
    assert estimate_answer_elements(fenced) == 1

    # segment 入口与文本估算一致
    seg = Segment(SegmentType.ANSWER, "answer")
    seg.text = table
    assert estimate_segment_elements(seg, []) == with_table


def test_estimate_tool_elements_counts_optional_detail_and_output_blocks() -> None:
    """面板估算必须等于真实渲染产物：单步成本直接数元素，不按常数折算。

    回归（2026-10-27）：常数口径每步按 5~7 记，实际渲染只有 1~3，
    账面余额虚高四成，剩余预算判据跟着失真。
    """
    steps = [
        _step(),
        _step(detail="path"),
        _step(result=True),
        _step(error=True),
    ]

    real = 3 + sum(count_card_elements(_build_tool_step_elements(s)) for s in steps)
    assert estimate_tool_elements(0, len(steps), steps) == real
    assert estimate_tool_elements(0, len(steps), steps) < 3 + 4 * 5  # 旧常数口径下界


def test_tool_segment_end_uses_open_segment_step_count() -> None:
    seg = Segment(SegmentType.TOOL, "tool")
    steps = [_step(), _step()]

    assert tool_segment_end(seg, steps) == 2

    seg.tool_end_offset = 1
    assert tool_segment_end(seg, steps) == 1


def test_find_tool_split_offset_keeps_largest_fitting_prefix() -> None:
    seg = Segment(SegmentType.TOOL, "tool")
    steps = [_step() for _ in range(4)]

    split_offset = find_tool_split_offset(
        base_count=ELEMENT_THRESHOLD - FOOTER_RESERVE - 7,
        seg=seg,
        all_steps=steps,
    )

    # 单步真实成本 1~3 格（拼常数口径时代是 5~7），余量够装 3 步
    assert split_offset == 3


def test_find_tool_split_offset_returns_none_for_single_step() -> None:
    seg = Segment(SegmentType.TOOL, "tool")

    assert find_tool_split_offset(base_count=1, seg=seg, all_steps=[_step()]) is None


def test_estimate_answer_elements_exact_table_expansion() -> None:
    """流式路径:表头行也渲染为单元格 — (1+数据行)*列 + 容器 + markdown 元素本身."""
    table = "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 | 6 |"
    # 流式: 1(markdown) + (1+2)*3(表头+数据格) + 1(容器) = 11;完成态仅 1(原生 table)
    assert estimate_answer_elements(table) == 11


def test_estimate_answer_elements_complete_path_dominant() -> None:
    """长文本场景完成态切块多于流式单元素,取大者."""
    # 完成态: 5 块(2400*4+400);流式: 1
    assert estimate_answer_elements("x" * 10000) == 5


def test_estimate_answer_elements_counts_images() -> None:
    """markdown 图片每张占 1 个 img 元素(流式路径)."""
    text = "图1 ![a](img_v3_1) 图2 ![b](img_v3_2) 图3 ![c](img_v3_3)"
    assert estimate_answer_elements(text) == 4


def test_estimate_answer_elements_mixed_table_and_text() -> None:
    """表格+长文混合:流式(单元格)与完成态(原生表格+切块)分别算,取大者."""
    table = "| A | B |\n|---|---|\n| 1 | 2 |"
    text = table + "\n\n" + "y" * 5000
    # 流式: 1 + (1+1)*2+1 = 6;完成态: 1(table) + 3(chunks) = 4 → 取 6
    assert estimate_answer_elements(text) == 6
