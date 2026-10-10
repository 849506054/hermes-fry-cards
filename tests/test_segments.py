"""streaming.segments 测试 — SegmentState 段管理、边界条件、多轮集成."""

from __future__ import annotations

import time

import pytest

from hermes_fry_cards.streaming import segments as segments_module
from hermes_fry_cards.streaming.segments import Segment, SegmentState, SegmentType


class TestSegmentDefaults:
    def test_all_defaults(self) -> None:
        for seg_type, el_id in [("reasoning", "r_0_panel"), ("answer", "a_0"), ("tool", "t_0")]:
            seg = Segment(seg_type, el_id)
            assert seg.type == seg_type
            assert seg.el_id == el_id
            assert seg.created is False
            assert seg.dirty is True
            assert seg.element_estimate == 0
            assert seg.text == ""
            assert seg.tool_offset == 0
            assert seg.tool_end_offset == 0
            assert seg.elapsed_ms == 0.0
            assert seg.reasoning_finalized is False

    def test_slots_no_dynamic_attr(self) -> None:
        seg = Segment("answer", "a_0")
        with pytest.raises(AttributeError):
            seg.nonexistent = True  # type: ignore[attr-defined]


# ── 段创建与追加（三种类型共享同一模式：空→新建、同类型→追加、异类型→新建） ──


class TestOnReasoningDelta:
    def test_appends_or_creates(self) -> None:
        state = SegmentState()
        state.on_reasoning_delta("hello ")
        state.on_reasoning_delta("world")
        assert len(state.segments) == 1
        assert state.segments[0].text == "hello world"

    def test_new_segment_when_last_is_answer(self) -> None:
        state = SegmentState()
        state.on_answer_delta("answer")
        state.on_reasoning_delta("thoughts")
        assert len(state.segments) == 2
        assert state.segments[0].type == "answer"
        assert state.segments[1].type == "reasoning"


class TestOnAnswerDelta:
    def test_appends_or_creates(self) -> None:
        state = SegmentState()
        state.on_answer_delta("hello ")
        state.on_answer_delta("world")
        assert len(state.segments) == 1
        assert state.segments[0].text == "hello world"

    def test_new_segment_when_last_is_reasoning(self) -> None:
        state = SegmentState()
        state.on_reasoning_delta("thinking")
        state.on_answer_delta("reply")
        assert len(state.segments) == 2
        assert state.segments[0].type == "reasoning"
        assert state.segments[1].type == "answer"

    def test_finalizes_prev_reasoning_elapsed(self) -> None:
        state = SegmentState()
        state.on_reasoning_delta("thinking")
        time.sleep(0.01)
        state.on_answer_delta("reply")
        assert state.segments[0].elapsed_ms > 0

    def test_does_not_finalize_already_finalized(self) -> None:
        state = SegmentState()
        state.on_reasoning_delta("a")
        state.on_answer_delta("b")
        elapsed_1 = state.segments[0].elapsed_ms
        state.on_reasoning_delta("c")
        state.on_answer_delta("d")
        assert state.segments[0].elapsed_ms == elapsed_1


class TestOnToolEvent:
    @pytest.mark.parametrize("count", [0, -1])
    def test_non_positive_returns_early(self, count: int) -> None:
        state = SegmentState()
        state.on_tool_event(count)
        assert state.segments == []

    def test_creates_and_marks_dirty(self) -> None:
        state = SegmentState()
        state.on_tool_event(3)
        assert len(state.segments) == 1
        assert state.segments[0].type == "tool"
        assert state.segments[0].tool_offset == 2
        assert state.segments[0].dirty is True

    def test_same_type_marks_dirty(self) -> None:
        state = SegmentState()
        state.on_tool_event(1)
        state.segments[0].dirty = False
        state.on_tool_event(2)
        assert len(state.segments) == 1
        assert state.segments[0].dirty is True

    def test_cross_type_creates_new_and_finalizes_prev(self) -> None:
        state = SegmentState()
        state.on_tool_event(2)  # tool1: offset=1
        state.on_answer_delta("intermediate")
        state.on_tool_event(5)  # tool2: offset=4, tool1.end=4
        assert state.segments[0].tool_end_offset == 4
        assert state.segments[2].tool_offset == 4


# ── finalize_segments ──


class TestFinalizeSegments:
    def test_finalizes_last_tool_and_reasoning(self) -> None:
        state = SegmentState()
        state.on_tool_event(1)
        state.on_reasoning_delta("thinking")
        time.sleep(0.001)
        state.finalize_segments(3)
        assert state.segments[0].tool_end_offset == 3
        assert state.segments[1].elapsed_ms > 0

    def test_no_segments_no_error(self) -> None:
        state = SegmentState()
        state.finalize_segments(0)

    def test_already_finalized_not_overwritten(self) -> None:
        state = SegmentState()
        state.on_tool_event(2)
        state.on_answer_delta("mid")
        state.on_tool_event(5)
        state.on_reasoning_delta("a")
        time.sleep(0.01)
        state.on_answer_delta("b")
        # segments: [tool(0), answer(1), tool(2), reasoning(3), answer(4)]
        elapsed_r1 = state.segments[3].elapsed_ms
        state.finalize_segments(10)
        assert state.segments[0].tool_end_offset == 4  # finalized by tool[2], not overwritten
        assert state.segments[2].tool_end_offset == 10  # finalized by finalize
        assert state.segments[3].elapsed_ms == elapsed_r1  # reasoning not overwritten


# ── has_dirty ──


class TestHasDirty:
    def test_dirty_lifecycle(self) -> None:
        state = SegmentState()
        assert state.has_dirty is False
        state.on_reasoning_delta("a")
        assert state.has_dirty is True
        state.segments[0].dirty = False
        assert state.has_dirty is False


# ── 多轮集成 ──


class TestMultiRound:
    def test_two_rounds(self) -> None:
        state = SegmentState()
        state.on_reasoning_delta("think 1")
        state.on_answer_delta("reply 1")
        state.on_tool_event(2)
        state.on_reasoning_delta("think 2")
        state.on_answer_delta("reply 2")
        types = [s.type for s in state.segments]
        assert types == ["reasoning", "answer", "tool", "reasoning", "answer"]

    def test_el_id_naming_persists(self) -> None:
        state = SegmentState()
        state.on_reasoning_delta("a")  # 0
        state.on_answer_delta("b")  # 1
        state.on_tool_event(1)  # 2
        state.on_reasoning_delta("c")  # 3
        assert state.segments[0].el_id == "reasoning_0_panel"
        assert state.segments[0].text_el_id == "reasoning_0_text"
        assert state.segments[1].el_id == "answer_1"
        assert state.segments[2].el_id == "tool_panel"
        assert state.segments[3].el_id == "reasoning_3_panel"

    def test_finalize_complex_scenario(self, monkeypatch: pytest.MonkeyPatch) -> None:
        times = iter(float(i) for i in range(100, 108))
        monkeypatch.setattr(segments_module.time, "time", lambda: next(times))

        state = SegmentState()
        state.on_reasoning_delta("r1")
        state.on_answer_delta("a1")
        state.on_tool_event(2)  # tool1: offset=1
        state.on_answer_delta("mid")
        state.on_tool_event(4)  # tool2: offset=3, tool1.end=3
        state.on_reasoning_delta("r2")
        state.on_answer_delta("a2")
        state.finalize_segments(5)

        assert state.segments[0].elapsed_ms > 0  # r1 finalized by a1
        assert state.segments[2].tool_end_offset == 3  # tool1 finalized by tool2
        assert state.segments[4].tool_end_offset == 5  # tool2 finalized by finalize
        assert state.segments[5].elapsed_ms > 0  # r2 finalized by a2


# ── answer 段防爆闸（回归：300305 疯狂拆卡）────────────────────────────


class TestAnswerSegmentCap:
    """answer↔tool 频繁交替时 answer 段数量必须有上限。

    回归：step-5-preview 这类每个工具调用前后都吐过渡语的模型，20 轮工具调用
    产生 20 个独立 answer 段（reasoning 有 max_reasoning_panels 闸、TOOL 段共享
    同一个 tool_panel element_id，唯独 answer 无上限），把 _counter 推到 82、
    卡片元素逼近飞书 200 硬上限 → 300305 反复失败 + 疯狂拆卡。
    """

    def test_answer_segments_capped_under_tool_alternation(self) -> None:
        state = SegmentState()
        for i in range(20):
            state.on_reasoning_delta(f"think {i} ")
            state.on_answer_delta(f"过渡语 {i} ")
            state.on_tool_event(i + 1)

        answers = [s for s in state.segments if s.type == SegmentType.ANSWER]
        assert len(answers) == state.max_answer_segments
        # 未加闸时这里是 20，segment 总数 43
        assert len(state.segments) < 25

    def test_cap_preserves_all_answer_text(self) -> None:
        state = SegmentState()
        for i in range(20):
            state.on_answer_delta(f"a{i} ")
            state.on_tool_event(i + 1)

        merged = "".join(s.text for s in state.segments if s.type == SegmentType.ANSWER)
        for i in range(20):
            assert f"a{i} " in merged, f"合并后丢了 a{i}"

    def test_short_conversations_keep_separate_segments(self) -> None:
        """未达上限时行为不变——既有契约（a1 → tool → mid 是独立段）必须保持。"""
        state = SegmentState()
        state.on_answer_delta("a1")
        state.on_tool_event(2)
        state.on_answer_delta("mid")
        assert [s.type for s in state.segments] == ["answer", "tool", "answer"]

    def test_cap_is_configurable(self) -> None:
        state = SegmentState(max_answer_segments=2)
        for i in range(6):
            state.on_answer_delta(f"a{i} ")
            state.on_tool_event(i + 1)
        assert sum(1 for s in state.segments if s.type == SegmentType.ANSWER) == 2


class TestCompletionOverlapDedup:
    """流式丢尾时 on_completion_answer 必须原地接上，而不是追加第二段（#19）。

    回归：长回复只流到 985/1097 字就收尾（丢尾）时，原 endswith 守卫只认
    「流式段以完整完成文本结尾」，判假 → 完整文本被 _new_answer 追加成第二段
    ANSWER，封卡后正文出现两遍。
    """

    @staticmethod
    def _answer_texts(state: SegmentState) -> list[str]:
        return [s.text for s in state.segments if s.type == SegmentType.ANSWER]

    def test_lagging_stream_merged_in_place(self) -> None:
        """场景 B：流式落后（本次事故形态）→ 单段完整文本."""
        final = "答案" + "正文" * 500
        state = SegmentState()
        state.on_answer_delta(final[:985])
        state.on_completion_answer(final)
        assert self._answer_texts(state) == [final]

    def test_fully_streamed_still_noop(self) -> None:
        """场景 A：流式已完整送达 → endswith 守卫吞掉，1 段不变."""
        final = "答案" + "正文" * 500
        state = SegmentState()
        state.on_answer_delta(final)
        state.on_completion_answer(final)
        assert self._answer_texts(state) == [final]

    def test_interlude_then_final_keeps_two_segments(self) -> None:
        """场景 C：工具间旁白 + 最终答案（#13 场景）→ 旁白保留，不往回合并."""
        interlude = "调用工具中"
        final = "答案" + "正文" * 500
        state = SegmentState()
        state.on_answer_delta(interlude)
        state.on_tool_event(1)
        state.on_completion_answer(final)
        assert self._answer_texts(state) == [interlude, final]

    def test_completion_replay_is_idempotent(self) -> None:
        """场景 D：完成回调重放同一文本 → 仍 1 段."""
        final = "答案" + "正文" * 500
        state = SegmentState()
        state.on_answer_delta(final[:985])
        state.on_completion_answer(final)
        state.on_completion_answer(final)
        assert self._answer_texts(state) == [final]

    def test_interlude_and_lagging_merged_via_overlap(self) -> None:
        """场景 E：旁白与落后答案同段 → 去重叠后原地接上，旁白不丢."""
        interlude = "过渡语。"
        final = "答案" + "正文" * 500
        state = SegmentState()
        state.on_answer_delta(interlude + final[:985])
        state.on_completion_answer(final)
        assert self._answer_texts(state) == [interlude + final]

    def test_short_coincidental_overlap_not_merged(self) -> None:
        """场景 F：偶然短重叠（<8 字）→ 不吞并，按新段追加."""
        state = SegmentState()
        state.on_answer_delta("第一段尾。")
        state.on_tool_event(1)
        state.on_completion_answer("。完全不同的完成文本(长度足够超过阈值)")
        assert self._answer_texts(state) == ["第一段尾。", "。完全不同的完成文本(长度足够超过阈值)"]
