"""思考时间线的滚动窗口预算.

飞书对整张卡片有载荷字节上限（超限返回 ``200860 card over max size``），
与元素数上限（300305）是两道独立的闸门。思考时间线里每一段思考都是独立的
标题 + 正文元素，长回合（几十段）累积到十几万字节就会先撞字节闸门：撞上后
连封卡都会被拒，整轮退回纯文本。

这里给出保尾部的窗口口径：段数与字节双闸门，超出时回收最早的那几段，
卡片体积因此有上界，不需要靠拆卡来腾地方。
"""
from __future__ import annotations

from collections.abc import Sequence

# 保留最近的思考段数（时间线里最新的这些留在一张卡上）。
REASONING_WINDOW_ROUNDS = 30
# 保留思考正文的字节上界；单段另有 _REASONING_MAX_BYTES 的尾部钳制。
REASONING_WINDOW_BYTES = 100_000
# 回收提示行固定 id：只建一次，之后原地改文案。
REASONING_TRIM_NOTE_ELEMENT_ID = "reasoning_trim_note"


def reasoning_evictions(sizes: Sequence[int]) -> int:
    """返回需要从头部回收的思考段数量（按段数与字节双闸门保尾部窗口）.

    ``sizes`` 为时间线上各思考段正文的字节数，按到达顺序排列。
    """
    kept = len(sizes)
    evict = 0
    total = sum(sizes)
    # 最新一段永远保留：只剩一段时不再回收，否则卡上会连一段思考都不剩。
    while evict < kept - 1 and (
        kept - evict > REASONING_WINDOW_ROUNDS or total > REASONING_WINDOW_BYTES
    ):
        total -= sizes[evict]
        evict += 1
    return evict
