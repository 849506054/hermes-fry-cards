"""Hermes 网关侧的 pending-clarify 登记器（随仓参考实现）.

真实部署里本模块随 Hermes 的 ``tools`` 包发布：agent 轮次（``run_turn_runner``
的 ``_ask_clarify_question``）调用 :func:`register` 登记提问、阻塞在
:func:`wait_for_response`，会话收尾（interrupt / run end / gateway shutdown）
调用 :func:`clear_session` 兜底唤醒；插件侧 ``hermes_fry_cards.clarify`` 惰性
导入本模块（缺失即整体降级），在卡片点击 / 内联输入 / 聊天文本拦截三条路径上
落定答案并唤醒等待。

API 契约与 ``tests/samples/`` 归档的真实 Hermes 源码用法一致；本仓保留一份
是为插件测试（``tests/test_clarify_buttons.py``）与部署排障提供可运行的参照。
"""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional

# _entries 封顶：与插件侧 _CLARIFY_STATE 的 FIFO 口径一致，悬挂轮次不无限累积。
_ENTRIES_CAP = 256
_DEFAULT_CLARIFY_TIMEOUT_SEC = 300.0  # 5 分钟，与网关 "did not respond within Nm" 的分钟口径一致
_TIMEOUT_ENV = "HERMES_CLARIFY_TIMEOUT_SEC"

# 落定哨兵（字符串形态，与答案同通道返回；比较按 ==）：
#   CANCELLED — 会话被拆（/new、打断、run 收尾）时由 clear_session 落定，
#               网关据此把 outcome 记为 cancelled 而不是 timed_out；
#   SKIPPED   — 用户主动跳过（跳过指令/按钮）时落定，批量提问按「该题无答」继续。
CANCELLED = "[clarify cancelled]"
SKIPPED = "[clarify skipped]"

_lock = threading.Lock()
_entries: Dict[str, "ClarifyEntry"] = {}


class ClarifyEntry:
    """一次待澄清提问；``response`` 即落定答案，``awaiting_text`` 表示自由文本模式."""

    __slots__ = (
        "clarify_id",
        "session_key",
        "question",
        "choices",
        "multi_select",
        "awaiting_text",
        "response",
        "_resolved",
    )

    def __init__(
        self,
        *,
        clarify_id: str,
        session_key: str = "",
        question: str = "",
        choices: Optional[List[str]] = None,
        multi_select: bool = False,
    ) -> None:
        self.clarify_id = clarify_id
        self.session_key = session_key
        self.question = question
        self.choices = list(choices or [])
        self.multi_select = bool(multi_select)
        # 无选项的开放提问从登记起就在自由文本模式（网关文本拦截直接按答案收）
        self.awaiting_text = not self.choices
        self.response: Optional[str] = None
        self._resolved = threading.Event()

    @property
    def resolved(self) -> bool:
        return self._resolved.is_set()


def register(
    *,
    clarify_id: str,
    session_key: str = "",
    question: str = "",
    choices: Optional[List[str]] = None,
    multi_select: bool = False,
) -> ClarifyEntry:
    """登记一次待澄清提问，返回登记条目；同 id 重复登记按新提问处理."""
    entry = ClarifyEntry(
        clarify_id=str(clarify_id),
        session_key=str(session_key or ""),
        question=str(question or ""),
        choices=[str(c) for c in (choices or [])],
        multi_select=bool(multi_select),
    )
    with _lock:
        if len(_entries) >= _ENTRIES_CAP:
            _drop_overflow(len(_entries) - _ENTRIES_CAP + 1)
        _entries[entry.clarify_id] = entry
    return entry


def _drop_overflow(count: int) -> None:
    """超限时先清已落定的旧条目，不够再 FIFO 淘汰；调用方持有 _lock."""
    for key in [k for k, v in _entries.items() if v.resolved][:count]:
        _entries.pop(key, None)
    while count > 0 and _entries:
        _entries.pop(next(iter(_entries)))
        count -= 1


def wait_for_response(clarify_id: str, timeout: float = 300.0) -> Optional[str]:
    """阻塞等答案；落定返回文本，超时或未知 id 返回 None."""
    with _lock:
        entry = _entries.get(str(clarify_id))
    if entry is None:
        return None
    entry._resolved.wait(timeout)
    return entry.response


def _resolve_entry(entry: "ClarifyEntry", text: str) -> bool:
    """落定一条提问；已落定的不重复落定（幂等 False）。调用方持有 _lock."""
    if entry.resolved:
        return False
    entry.response = str(text or "")
    entry._resolved.set()
    return True


def resolve_gateway_clarify(clarify_id: str, text: str) -> bool:
    """按 clarify_id 落定答案（按钮选择 / 内联输入 / 文本拦截共用）.

    未知 id 或已落定返回 False，插件据此保留原卡、网关据此放行消息。
    """
    with _lock:
        entry = _entries.get(str(clarify_id))
        if entry is None:
            return False
        return _resolve_entry(entry, text)


def resolve_text_response_for_session(session_key: str, text: str) -> bool:
    """聊天文本拦截路径：按会话键落定处于自由文本模式的提问."""
    pending = get_pending_for_session(session_key)
    if pending is None:
        return False
    with _lock:
        return _resolve_entry(pending, text)


def mark_awaiting_text(clarify_id: str) -> bool:
    """"其他"点击：把提问翻成自由文本模式；未知或已落定返回 False."""
    with _lock:
        entry = _entries.get(str(clarify_id))
        if entry is None or entry.resolved:
            return False
        entry.awaiting_text = True
        return True


def get_pending_for_session(session_key: str) -> Optional[ClarifyEntry]:
    """取会话里最新一条「等自由文本」的未落定提问；没有返回 None."""
    key = str(session_key or "")
    with _lock:
        found: Optional[ClarifyEntry] = None
        for entry in _entries.values():
            if entry.session_key == key and not entry.resolved and entry.awaiting_text:
                found = entry
        return found


def clear_session(session_key: str) -> None:
    """把会话内全部未落定提问按 CANCELLED 落定并唤醒等待者；幂等.

    会话收尾（interrupt / run end / gateway shutdown）时调用，阻塞的 agent
    线程拿到 CANCELLED 哨兵收场，不会挂过轮次结束。
    """
    key = str(session_key or "")
    if not key:
        return
    with _lock:
        for entry in _entries.values():
            if entry.session_key == key:
                _resolve_entry(entry, CANCELLED)


def get_clarify_timeout() -> float:
    """澄清等待时长（秒）：环境变量可覆盖，默认 5 分钟."""
    try:
        value = float(os.environ.get(_TIMEOUT_ENV, ""))
    except ValueError:
        return _DEFAULT_CLARIFY_TIMEOUT_SEC
    return value if value > 0 else _DEFAULT_CLARIFY_TIMEOUT_SEC
