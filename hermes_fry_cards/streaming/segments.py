"""流式卡片状态追踪 — 扁平 segments 管理."""

from __future__ import annotations

import time
from enum import StrEnum


class SegmentType(StrEnum):
    REASONING = "reasoning"
    ANSWER = "answer"
    TOOL = "tool"


# 判定「流式段尾与完成文本开头重叠」所需的最小重叠长度：低于此值多为偶然同字，
# 按新段追加（保留工具间旁白语义）；落后流式的真实重叠通常达数百字。
_MIN_OVERLAP_TAIL = 8


class Segment:
    """单个内容段 — reasoning / answer / tool."""

    __slots__ = (
        "created",
        "dirty",
        "el_id",
        "elapsed_ms",
        "element_estimate",
        "reasoning_finalized",
        "start_time",
        "text",
        "text_el_id",
        "tool_end_offset",
        "tool_offset",
        "type",
    )

    def __init__(self, seg_type: SegmentType | str, el_id: str) -> None:
        self.type = SegmentType(seg_type)
        self.el_id = el_id
        self.created = False
        self.dirty = True
        self.element_estimate: int = 0
        self.text: str = ""
        self.text_el_id: str = ""
        self.tool_offset: int = 0
        self.tool_end_offset: int = 0  # 0 = 未终结；>= 1 表示已终结
        self.start_time: float = 0.0
        self.elapsed_ms: float = 0.0
        self.reasoning_finalized: bool = False


class NotificationSlot:
    """一条待渲染到卡片通知区的提示（Hermes 原文）.

    不占卡片元素：流式态由 loading 图标淡出表示，完成态由 builder 统一渲染在
    完成态卡片顶部。``kind`` 仅用于日志，不参与文案。
    """

    __slots__ = ("kind", "text")

    def __init__(self, text: str, kind: str = "notice") -> None:
        self.text = text
        self.kind = kind


class SegmentState:
    """管理单张流式卡片的扁平内容段列表.

    纯数据类，不含 IO。每个 segment 是一个内容块（reasoning/answer/tool），
    按事件到达顺序排列，无需推断轮次边界。
    """

    __slots__ = (
        "_completion_answers",
        "_counter",
        "notices",
        "segments",
        "max_reasoning_panels",
        "max_answer_segments",
    )

    def __init__(self, max_reasoning_panels: int = 3, max_answer_segments: int = 8) -> None:
        self._counter = 0
        self._completion_answers: set[str] = set()
        self.segments: list[Segment] = []
        self.notices: list[NotificationSlot] = []
        # 限制独立 reasoning 面板数量，超出后合并进最后一个 reasoning 面板，
        # 避免「不支持分段思考」的模型频繁切换 thinking/tool 时产生海量面板导致卡片元素溢出。
        self.max_reasoning_panels = max_reasoning_panels
        # 限制独立 answer 段数量，超出后合并进最后一个 answer 段。与上面同源：
        # answer↔tool 频繁交替时每个 answer 段至少占 1 个卡片元素，而 TOOL 段共享
        # 同一个 tool_panel element_id 只占一个面板——不设上限的只有 answer，
        # 20 轮工具调用就能产生 20 个 answer 段把卡片元素推向飞书 200 硬上限。
        self.max_answer_segments = max_answer_segments

    def add_notice(self, text: str) -> None:
        """记录一条状态提示（Hermes 原文），随完成态卡片一起渲染.

        #43：忙碌回执 / 重定向 / 后台回执由 Hermes 自己合成文案，插件只负责把它
        写进当前卡片的通知槽，不另写文案、不另发纯文本。展示位置与 i18n 标签
        由 builder 决定（``NotificationSlot``）。
        """
        cleaned = text.strip()
        if not cleaned:
            return
        if not any(slot.text == cleaned for slot in self.notices):
            self.notices.append(NotificationSlot(cleaned))

    def _new_reasoning(self, text: str) -> Segment:
        c = self._counter
        self._counter += 1
        seg = Segment(SegmentType.REASONING, f"reasoning_{c}_panel")
        seg.text_el_id = f"reasoning_{c}_text"
        seg.text = text
        seg.start_time = time.time()
        self.segments.append(seg)
        return seg

    def _new_answer(self, text: str) -> Segment:
        c = self._counter
        self._counter += 1
        seg = Segment(SegmentType.ANSWER, f"answer_{c}")
        seg.text = text
        seg.start_time = time.time()
        self._finalize_prev_reasoning(seg.start_time)
        self.segments.append(seg)
        return seg

    def _new_tool(self, tool_offset: int) -> Segment:
        # 所有 TOOL segment 共享同一个 element_id，合并成一个底部面板
        c = self._counter
        self._counter += 1
        seg = Segment(SegmentType.TOOL, "tool_panel")
        seg.tool_offset = tool_offset
        seg.start_time = time.time()
        self._finalize_prev_reasoning(seg.start_time)
        self.segments.append(seg)
        return seg

    def _finalize_prev_reasoning(self, now: float) -> None:
        """终结最后一个未计算耗时的 reasoning segment."""
        for seg in reversed(self.segments):
            if seg.type == SegmentType.REASONING and seg.start_time and not seg.elapsed_ms:
                seg.elapsed_ms = (now - seg.start_time) * 1000
                break

    def on_reasoning_delta(self, text: str) -> None:
        """处理 reasoning 增量，同类型追加否则新建 segment."""
        if self.segments and self.segments[-1].type == SegmentType.REASONING:
            self.segments[-1].text += text
            self.segments[-1].dirty = True
            return

        # 达到 reasoning 面板上限：不新建面板，把文本合并进最后一个 reasoning 面板，
        # 避免 thinking/tool 频繁交替时产生海量独立面板导致元素溢出。
        reasoning_count = sum(1 for s in self.segments if s.type == SegmentType.REASONING)
        if reasoning_count >= self.max_reasoning_panels:
            for seg in reversed(self.segments):
                if seg.type == SegmentType.REASONING:
                    seg.text += text
                    seg.dirty = True
                    return
        self._new_reasoning(text)

    def on_answer_delta(self, text: str) -> None:
        """处理 answer 增量，同类型追加否则新建 segment."""
        if self.segments and self.segments[-1].type == SegmentType.ANSWER:
            self.segments[-1].text += text
            self.segments[-1].dirty = True
            return

        # 与 reasoning 面板同源的防爆闸。answer↔tool 频繁交替时（每个工具调用前后
        # 都吐一句过渡语的模型，典型如 step-5-preview）每次交替都会新建一个 answer
        # segment，而每个 answer 段在卡片上至少占 1 个元素。实测 20 轮工具调用产生
        # 20 个 answer 段（reasoning 已被 max_reasoning_panels 闸住、TOOL 段共享同一个
        # tool_panel element_id 只占一个面板，唯独 answer 无上限），把 _counter 推到
        # 82、卡片元素逼近飞书 200 硬上限，表现为 300305 反复失败 + 疯狂拆卡。
        # 达到上限后合并进最后一个 answer 段：内容不丢、元素数不涨。
        answer_count = sum(1 for s in self.segments if s.type == SegmentType.ANSWER)
        if answer_count >= self.max_answer_segments:
            for seg in reversed(self.segments):
                if seg.type == SegmentType.ANSWER:
                    seg.text += text
                    seg.dirty = True
                    return
        self._new_answer(text)

    def on_completion_answer(self, text: str, *, replaced: bool = False) -> None:
        """Append final completion text once, preserving interim answer segments.

        ``replaced=True`` = 本轮答案被收尾闸门打回后重写（调用方从 finish_reason 判定）：
        流式段里那段旧稿不是合法历史，用定稿整段替换，否则封卡后正文出现旧稿+定稿两段。
        """
        if not text or text in self._completion_answers:
            return
        if replaced:
            for segment in reversed(self.segments):
                if segment.type != SegmentType.ANSWER:
                    continue
                self._completion_answers.add(text)
                segment.text = text
                segment.dirty = True
                return
        elif any(
            segment.type == SegmentType.ANSWER and segment.text.endswith(text)
            for segment in self.segments
        ):
            self._completion_answers.add(text)
            return

        # 流式落后（卡片只推到答案前缀就收尾）时，完成文本就是同一段答案的完整版本：
        # 与已有 ANSWER 段去重叠后原地接上，而不是再追加一段（否则封卡后正文出现两遍）。
        #
        # 多段合并：reasoning / tool 会把一轮回复切成多个 answer 段，而 final_response 是
        # 全文。旧实现只跟「最后一个」answer 段去重，前面的段仍留着被 final 覆盖的开头，
        # 封卡后开头（表格+正文）出现两遍。判据用「按序拼接的段文本是 final 的子序列」：
        # 流式前缀可能缺行（表格分隔行还没推到）而非严格前缀，子序列容得下这种差异。
        _answer_segs = [s for s in self.segments if s.type == SegmentType.ANSWER and s.text]
        if len(_answer_segs) >= 2:
            _joined = "".join(s.text for s in _answer_segs)
            _it = iter(text)
            if all(_ch in _it for _ch in _joined):
                _first, *_rest = _answer_segs
                _first.text = text
                _first.dirty = True
                for _stale in _rest:
                    _stale.text = ""
                    _stale.dirty = False
                    _stale.created = False
                self._completion_answers.add(text)
                return

        for segment in reversed(self.segments):
            if segment.type != SegmentType.ANSWER:
                continue
            if text.startswith(segment.text):
                self._completion_answers.add(text)
                segment.text = text
                segment.dirty = True
                return
            for overlap in range(len(segment.text), _MIN_OVERLAP_TAIL - 1, -1):
                if segment.text.endswith(text[:overlap]):
                    self._completion_answers.add(text)
                    segment.text = segment.text[:-overlap] + text
                    segment.dirty = True
                    return
            break

        self._completion_answers.add(text)
        self._new_answer(text)

    def on_tool_event(self, tool_step_count: int) -> None:
        """处理工具调用事件，同类型标记 dirty 否则新建 segment 并终结前序 tool segment."""
        if tool_step_count <= 0:
            return
        if self.segments and self.segments[-1].type == SegmentType.TOOL:
            self.segments[-1].dirty = True
            return
        for seg in reversed(self.segments):
            if seg.type == SegmentType.TOOL and seg.tool_end_offset == 0:
                seg.tool_end_offset = tool_step_count - 1
                seg.dirty = True
                break
        self._new_tool(tool_step_count - 1)

    def split_tool_segment(
        self,
        index: int,
        split_tool_offset: int,
    ) -> Segment:
        """在 step 边界拆分一个 tool segment，返回承接后续 steps 的新 segment."""
        seg = self.segments[index]
        self._counter += 1
        # 所有 TOOL segment 共享底部同一个面板元素（同 _new_tool 的 "tool_panel"，
        # 即 cardkit.builder.TOOL_PANEL_ELEMENT_ID）：拆段只是把一个面板拆成两段
        # 渲染，不能另起 tools_N 元素 id —— 卡片上没有那个元素，后续批更新会报
        # 300313 not find elementID 并陷入「回滚重建 → 再回落」的抖动。
        new_seg = Segment(SegmentType.TOOL, "tool_panel")
        new_seg.tool_offset = split_tool_offset
        new_seg.tool_end_offset = seg.tool_end_offset
        new_seg.start_time = seg.start_time
        seg.tool_end_offset = split_tool_offset
        seg.dirty = True
        self.segments.insert(index + 1, new_seg)
        return new_seg

    def finalize_segments(self, total_tool_count: int) -> None:
        """完成态调用：终结最后一个 tool segment + 补算最后一个 reasoning elapsed_ms."""
        now = time.time()
        for seg in reversed(self.segments):
            if seg.type == SegmentType.TOOL and seg.tool_end_offset == 0:
                seg.tool_end_offset = total_tool_count
                break

        for seg in reversed(self.segments):
            if seg.type == SegmentType.REASONING and seg.start_time and not seg.elapsed_ms:
                seg.elapsed_ms = (now - seg.start_time) * 1000
                break

    @property
    def has_dirty(self) -> bool:
        """是否有需要 flush 的脏段."""
        return any(seg.dirty for seg in self.segments)
