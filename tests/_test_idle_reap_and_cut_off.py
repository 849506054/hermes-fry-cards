"""空闲回收与「被打断」文案的回归测试。

跑法（fork 根目录）：PYTHONPATH=. python3 tests/_test_idle_reap_and_cut_off.py

覆盖三件事，全部不依赖 Hermes 与飞书网络：
1. 会话的空闲基准由 `touch()` 推进，回收判据量的是空闲时长；
2. 思考阶段建段那一刻就带正文首屏，空段仍留占位空格；
3. 封卡无正文时，有过程落「被打断」文案，无过程才留 Done.。
"""

import asyncio
import inspect
import sys
import time

sys.path.insert(0, ".")

from hermes_fry_cards.cardkit.builder import build_complete_card  # noqa: E402
from hermes_fry_cards.cardkit.i18n import _T  # noqa: E402
from hermes_fry_cards.controller import StreamCardController  # noqa: E402
from hermes_fry_cards.streaming.segment_helper import (  # noqa: E402
    _REASONING_INITIAL_BYTES,
    build_add_segment_action,
)
from hermes_fry_cards.streaming.segments import SegmentState  # noqa: E402
from hermes_fry_cards.streaming.session import CardSession  # noqa: E402

BODY = "先看当前版本与上游 tag，确认这次跨了多少个版本，再决定要不要拆卡。"
FAILS = []


def check(name, cond):
    print("%-46s %s" % (name, "ok" if cond else "FAIL"))
    if not cond:
        FAILS.append(name)


# 1) touch() 推进空闲基准
loop = asyncio.new_event_loop()
sess = CardSession("om_test", "oc_test", loop)
check("last_activity 初值存在", isinstance(sess.last_activity, float))
time.sleep(0.01)
before = sess.last_activity
sess.touch()
check("touch() 推进 last_activity", sess.last_activity > before)
check("slots 声明含 last_activity", "last_activity" in CardSession.__slots__)

# 回收判据取空闲时长，且回收时封口
prune_src = inspect.getsource(StreamCardController._prune_stale_sessions)
check("回收判据用 last_activity", "last_activity" in prune_src and "created_at" not in prune_src)
check("回收时封被弃的卡", hasattr(StreamCardController, "_seal_abandoned_card")
      and "_seal_abandoned_card" in prune_src)
resolve_src = inspect.getsource(StreamCardController._resolve_session)
check("内容入口统一续命", "touch()" in resolve_src)

# 2) 建段即带正文首屏
state = SegmentState()
seg = state._new_reasoning(BODY)
seg.elapsed_ms = 1400
elements = build_add_segment_action(seg, [], reasoning_timeline=True)["params"]["elements"]
check("标题仍是耗时行", "思考了" in elements[0]["content"])
check("正文首屏随 add_elements 发出", elements[1]["content"].strip() == BODY[: len(elements[1]["content"].strip())]
      and elements[1]["content"].strip() != "")
check("首屏带正文 element_id", elements[1].get("element_id") == seg.text_el_id)
check("首屏不超预算", len(elements[1]["content"].encode("utf-8")) <= _REASONING_INITIAL_BYTES)

blank = SegmentState()._new_reasoning("   ")
blank_elements = build_add_segment_action(blank, [], reasoning_timeline=True)["params"]["elements"]
check("空段仍留占位空格", blank_elements[1]["content"] == " ")

# 3) 无正文封卡的文案分档
cut_state = SegmentState()
cut_state._new_reasoning(BODY).elapsed_ms = 900
cut_texts = [e.get("content", "") for e in
             build_complete_card(segments=cut_state.segments, all_tool_steps=[])["body"]["elements"]
             if e.get("tag") == "markdown"]
check("有思考无正文 → 被打断文案", any("cut off" in t for t in cut_texts))
check("被打断文案带中英双语", any("被打断" in str((e.get("i18n_content") or {}).get("zh_cn", ""))
                              for e in build_complete_card(segments=cut_state.segments,
                                                           all_tool_steps=[])["body"]["elements"]))

empty_texts = [e.get("content", "") for e in
               build_complete_card(segments=SegmentState().segments, all_tool_steps=[])["body"]["elements"]
               if e.get("tag") == "markdown"]
check("整轮无过程 → 仍留 Done.", any(t == _T["done"][0] for t in empty_texts)
      and not any("cut off" in t for t in empty_texts))

answer_state = SegmentState()
answer_state._new_reasoning(BODY).elapsed_ms = 900
answer_state._new_answer("这是正文内容。")
answer_texts = [e.get("content", "") for e in
                build_complete_card(segments=answer_state.segments, all_tool_steps=[])["body"]["elements"]
                if e.get("tag") == "markdown"]
check("有正文时两种兜底文案都不出现",
      not any(("cut off" in t) or (t == _T["done"][0]) for t in answer_texts))

print("\nSELF-CHECK %s" % ("OK" if not FAILS else "FAIL: " + ", ".join(FAILS)))
sys.exit(1 if FAILS else 0)
