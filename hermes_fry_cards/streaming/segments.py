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

    不占卡片元素：流式态由 loading 图标淡出表示，完成态由 builder 统一渲染。
    ``kind`` 决定完成态的展示位（#43 反馈）：
    - ``busy`` / ``redirect``：本轮仍在进行的提示 → 卡片底部（footer 之前）；
    - ``background``：完成后的后台回执 → 卡片顶部。
    """

    __slots__ = ("kind", "text")

    def __init__(self, text: str, kind: str = "notice") -> None:
        self.text = text
        self.kind = kind


# 完成后到达、展示在卡片顶部的提示种类；其余（忙碌/重定向等本轮进行中的
# 提示）一律落底部，避免和工具输出混在一起（#43 反馈）。
POST_TURN_NOTICE_KINDS = frozenset({"background"})


class SegmentState:
    """管理单张流式卡片的扁平内容段列表.

    纯数据类，不含 IO。每个 segment 是一个内容块（reasoning/answer/tool），
    按事件到达顺序排列，无需推断轮次边界。
    """

    __slots__ = (
        "_completion_answers",
        "_counter",
        "_notice_live_seq",
        "notices",
        "segments",
        "max_reasoning_panels",
        "max_answer_segments",
        "notice_element_id",
        "notice_element_text",
    )

    def __init__(self, max_reasoning_panels: int = 3, max_answer_segments: int = 8) -> None:
        self._counter = 0
        self._completion_answers: set[str] = set()
        self.segments: list[Segment] = []
        self.notices: list[NotificationSlot] = []
        # 建卡时画进卡片、可被首段正文原地接管的提示元素（#issue：提示元素未被正文覆盖）。
        # ``notice_element_id`` 为元素 id，``notice_element_text`` 为此刻画在它上面的文案；
        # 两者皆空表示本卡没有提示元素（例如回合中途才到的忙碌回执），此时无从「替换」。
        self.notice_element_id: str | None = None
        self.notice_element_text: str | None = None
        # 流式期中途补画通知时给新元素派 id 用的序号（``fry_notice_live_1`` …）：
        # 建卡时的提示元素已被正文接管或本卡从未带提示，中途到达的回执只能另起一个。
        self._notice_live_seq = 0
        # 限制独立 reasoning 面板数量，超出后合并进最后一个 reasoning 面板，
        # 避免「不支持分段思考」的模型频繁切换 thinking/tool 时产生海量面板导致卡片元素溢出。
        self.max_reasoning_panels = max_reasoning_panels
        # 限制独立 answer 段数量，超出后合并进最后一个 answer 段。与上面同源：
        # answer↔tool 频繁交替时每个 answer 段至少占 1 个卡片元素，而 TOOL 段共享
        # 同一个 tool_panel element_id 只占一个面板——不设上限的只有 answer，
        # 20 轮工具调用就能产生 20 个 answer 段把卡片元素推向飞书 200 硬上限。
        self.max_answer_segments = max_answer_segments

    def add_notice(self, text: str, kind: str = "notice") -> None:
        """记录一条状态提示（Hermes 原文），随完成态卡片一起渲染.

        #43：忙碌回执 / 重定向 / 后台回执由 Hermes 自己合成文案，插件只负责把它
        写进当前卡片的通知槽，不另写文案、不另发纯文本。``kind`` 决定完成态展示位，
        展示位置与渲染由 builder 决定（``NotificationSlot``）。
        """
        cleaned = text.strip()
        if not cleaned:
            return
        # 单槽位：后到的提示覆盖先到的（用户口径）。同一时刻卡上最多一条提示，
        # 忙碌回执 / 重定向 / 后台回执都走这一格，不做堆叠。
        if self.notices and self.notices[-1].text == cleaned and self.notices[-1].kind == kind:
            return
        self.notices = [NotificationSlot(cleaned, kind)]

    def bind_notice_element(self, element_id: str | None, text: str | None) -> None:
        """登记建卡时画进卡片的提示元素（``None`` = 这张卡没有提示元素）.

        #issue（提示元素未被正文覆盖）：提示是卡上真实存在的元素，正文到达时要在
        原地接管它；控制器建卡时把「元素 id + 画的哪条文案」登记到这里，段状态由
        此知道能不能接管、接管的又是哪一条提示。
        """
        self.notice_element_id = element_id or None
        self.notice_element_text = (text or "").strip() or None

    def notice_slot_target(self) -> str | None:
        """流式态通知槽此刻应显示的文案：最新到达的那条提示（无提示返回 ``None``）.

        #issue（流式期回执不可见）：提示只是记进了 ``notices``，卡上那个元素一直停在
        建卡时的文案。控制器每轮 flush 拿这里的目标文案和 ``notice_element_text``
        对比，不一致就补一次元素更新——回执到达当下即可见，不必等封卡。
        """
        return self.notices[-1].text if self.notices else None

    def needs_notice_slot_paint(self) -> bool:
        """通知槽要不要重画：目标文案与此刻画在元素上的不一致."""
        target = self.notice_slot_target()
        return target is not None and target != self.notice_element_text

    def next_notice_slot_element_id(self) -> str:
        """给通知槽派一个新元素 id（本卡没有可复用的提示元素时）.

        与建卡时的 ``fry_notice`` 分属两个命名空间：正文接管后 ``fry_notice`` 已是正文
        元素，再往它上面画提示会把正文冲掉。
        """
        self._notice_live_seq += 1
        return f"fry_notice_live_{self._notice_live_seq}"

    def adoptable_notice_element_id(self) -> str | None:
        """返回可被首段正文原地接管的提示元素 id；不可接管时返回 ``None``.

        只有「本轮进行中」的提示（忙碌回执 / 重定向）会被正文接管：它们只是过程
        说明，正文一到就该变成正文。后台完成回执属于回合后的通知，保持原样留在
        通知区顶部。
        """
        element_id, painted = self.notice_element_id, self.notice_element_text
        if not element_id or not painted:
            return None
        slot = next((s for s in self.notices if s.text == painted), None)
        if slot is None or slot.kind in POST_TURN_NOTICE_KINDS:
            return None
        # 已经出现过正文就不接管的了：接管只针对「首段 answer」。
        if any(seg.type == SegmentType.ANSWER and seg.text for seg in self.segments):
            return None
        return element_id

    def _new_answer_adopting_notice(self, text: str) -> Segment | None:
        """首段正文接管卡片提示元素（复用同一 element id）.

        接管后卡上只有一段正文，不会出现「提示一段 + 正文一段」；那条提示同时从
        通知槽里摘掉，完成态不再复述。
        """
        element_id = self.adoptable_notice_element_id()
        if element_id is None:
            return None
        seg = Segment(SegmentType.ANSWER, element_id)
        seg.text = text
        seg.start_time = time.time()
        seg.created = True  # 元素已在卡上（建卡时写的是提示文案），flush 只需流式覆盖
        seg.element_estimate = 1  # 该元素已由建卡时的提示记账计入
        self._finalize_prev_reasoning(seg.start_time)
        self.segments.append(seg)
        self.notice_element_id = None
        self.notice_element_text = None
        self.notices = [slot for slot in self.notices if slot.kind in POST_TURN_NOTICE_KINDS]
        return seg

    def reassign_element_id(self, seg: Segment) -> None:
        """给一个待重新创建的 answer 段换一个未占用的 element id.

        接管过提示元素的正文段带着 ``fry_notice`` 这个 id；换卡时新卡可能重新用
        ``fry_notice`` 承载提示，重新上卡前必须换 id，否则同一张卡里出现两个同 id
        元素（飞书报 not find / 重复 id）。只在「确实要重新创建这个元素」时调用。
        """
        seg.el_id = f"answer_{self._counter}"
        self._counter += 1

    def drop_rendered_progress_notices(self) -> None:
        """封卡前摘掉「已经画在卡片提示元素上」的那条提示（含后台完成回执）.

        #issue：这类提示占了卡上那个元素，完成态再渲染一遍就成了正文末尾的残留。
        用户口径是「流式时显示，封卡后被正文覆盖」——所以只看有没有真的画到卡上，
        不看种类：画过的那条一律从完成态正文里剔除，位置留给正文。被正文接管的情况
        下通知槽里早已没有它（no-op）；没被接管（本轮没有正文）时在这里摘掉。
        从未落到卡片元素上的提示没有元素可替换，仍按原样保留在完成态通知位——否则
        用户就再也看不到它了。
        """
        painted = self.notice_element_text
        self.notice_element_id = None
        self.notice_element_text = None
        # 封卡后卡片上只显示正文：本轮确实有正文时，通知槽里的提示一律不渲染
        # （单槽位，最多一条）。本轮没有正文（被打断 / 只跑了工具）时保留那一条，
        # 否则用户什么信息都拿不到。
        has_body = any(
            seg.type == SegmentType.ANSWER and (seg.text or "").strip()
            for seg in self.segments
        )
        if has_body:
            self.notices = []
        elif painted:
            self.notices = [slot for slot in self.notices if slot.text == painted]

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
        # 首段正文接管卡上的提示元素（#issue）：与其在提示旁边新增一段，不如原地
        # 把那一段变成正文，封卡后正文末尾不会残留一条进行中提示。
        if self._new_answer_adopting_notice(text) is not None:
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

    def split_notices(self) -> tuple[list[str], list[str]]:
        """按展示位拆分提示（#43 反馈）：(顶部=完成后回执, 底部=进行中提示).

        后台回执这类「回合结束后」的通知放顶部；忙碌回执 / 重定向这类
        「本轮还在进行」的提示落底部（footer 之前），不和工具输出混在一起。
        各自保持到达顺序。
        """
        top = [slot.text for slot in self.notices if slot.kind in POST_TURN_NOTICE_KINDS]
        bottom = [slot.text for slot in self.notices if slot.kind not in POST_TURN_NOTICE_KINDS]
        return top, bottom
