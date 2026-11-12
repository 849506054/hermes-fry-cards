"""拆卡交接卡不建计数框（用户口径，同停止卡）.

The card that hands the rest of a turn to a newer card keeps only the
「本轮继续。正文见后续卡片。」 line: its body text already moved to the next
card, so a collapsed panel with counts there has nothing to open.
"""

from __future__ import annotations

from hermes_fry_cards.cardkit.builder import build_complete_card
from hermes_fry_cards.streaming.segments import SegmentState


def _elements(card):
    return card["body"]["elements"]


def _has_panel(card) -> bool:
    stack = list(_elements(card))
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("tag") == "collapsible_panel":
            return True
        stack.extend(node.get("elements") or [])
        columns = node.get("columns") or []
        for column in columns:
            stack.extend(column.get("elements") or [])
    return False


def _walk(node):
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            for key in ("elements", "columns", "fields"):
                value = cur.get(key)
                if isinstance(value, list):
                    stack.extend(value)
        elif isinstance(cur, list):
            stack.extend(cur)


def _continued_lines(card) -> str:
    texts = []
    for node in _walk(card.get("body") or {}):
        i18n = node.get("i18n_content")
        if isinstance(i18n, dict) and i18n.get("zh_cn"):
            texts.append(str(i18n["zh_cn"]))
        elif isinstance(node.get("content"), str):
            texts.append(node["content"])
    return "\n".join(texts)


def _reasoning_segments() -> SegmentState:
    state = SegmentState()
    state._new_reasoning("想了一会儿")
    return state


def test_continuation_card_has_no_counts_panel():
    state = _reasoning_segments()
    card = build_complete_card(
        segments=state.segments,
        all_tool_steps=[],
        is_continuation=True,
        footer_data={"duration": 12.0, "tokens_per_sec": 80},
        panel_fields=["elapsed", "speed", "context"],
        reasoning_count=3,
        tool_count=5,
    )
    assert not _has_panel(card)
    assert "本轮继续" in _continued_lines(card)


def test_completed_card_keeps_counts_panel():
    state = _reasoning_segments()
    card = build_complete_card(
        segments=state.segments,
        all_tool_steps=[],
        footer_data={"duration": 12.0, "tokens_per_sec": 80},
        panel_fields=["elapsed", "speed", "context"],
        reasoning_count=3,
        tool_count=5,
    )
    assert _has_panel(card)


def test_aborted_card_has_no_counts_panel():
    state = _reasoning_segments()
    card = build_complete_card(
        segments=state.segments,
        all_tool_steps=[],
        is_aborted=True,
        reasoning_count=3,
        tool_count=5,
    )
    assert not _has_panel(card)
