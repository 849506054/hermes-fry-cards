"""AST Patch 注入的 Hook 函数.

这些函数从 gateway/run.py 的注入点被调用.
它们只做一件事：检查配置 → 调用 controller.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any

from .controller import get_controller

_logger = logging.getLogger("hermes_fry_cards")

# ── 群聊安全边界 ────────────────────────────────────────────────────────────
# Opt-in via gateway.group_security_boundary.enabled (default off). When on and a reply is
# headed to a GROUP chat (bot answering group members, not a DM), append an output-boundary note
# to the ephemeral system prompt so the model keeps secrets/private facts out of the shared
# channel. ``allow_chats`` is an EXEMPT list (dev/collaboration groups) — a group in it is skipped
# even when the boundary is enabled, so multiple agents can exchange credentials/internal state.
# DMs are never affected. This rides the ephemeral prompt (never mutates the persistent/cached
# system prompt), and lives here (not in hermes core) so it survives ``hermes update`` (re-applied
# by ``hermes_fry_cards install``).
_GROUP_SECURITY_BOUNDARY_DEFAULT = (
    "This message came from a GROUP chat where other people can read every reply. "
    "You must observe a strict output boundary: do not reveal API keys, passwords, tokens, "
    "server/internal IP addresses, credentials, file paths to private data, or any of the "
    "user's private/personal information to this group. Do not run or volunteer sensitive "
    "lookups (accounts, balances, credentials, internal state) unprompted. If a group member "
    "asks for such information, decline and say you can't share it here; offer to continue in a "
    "private chat with the owner. Keep group replies appropriate for a shared, public channel."
)


def apply_group_security_boundary(combined: str, user_config: Any, source: Any) -> str:
    """Return ``combined`` with the group-security-boundary note appended, if applicable.

    Pure-ish helper invoked from the injected ephemeral-prompt hook. Kept here (not in hermes
    core) so ``hermes update`` cannot wipe it; fry's ``install`` re-injects the call site.
    """
    try:
        gsb = (user_config.get("gateway") or {}).get("group_security_boundary") or {}
        enabled = bool(gsb.get("enabled"))
        allow = set(gsb.get("allow_chats") or [])
    except Exception:
        enabled = False
        allow = set()
    if not enabled:
        return combined
    chat_type = str(getattr(source, "chat_type", "") or "")
    chat_id = str(getattr(source, "chat_id", "") or "")
    if chat_type != "group" or chat_id in allow:
        return combined
    try:
        extra = ((user_config.get("gateway") or {}).get("group_security_boundary") or {}).get("text")
    except Exception:
        extra = ""
    note = (extra or _GROUP_SECURITY_BOUNDARY_DEFAULT).strip()
    if not note:
        return combined
    return (combined + "\n\n" + note).strip()



def _safe_hook(
    default_return: Any = None,
    log_level: str = "warning",
) -> Callable:
    """统一处理 enabled 检查和异常捕获."""

    def decorator(func: Callable) -> Callable:
        if iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*, message_id: str, **kwargs: Any) -> Any:
                try:
                    ctrl = get_controller()
                    if not ctrl.enabled:
                        return default_return
                    return await func(ctrl=ctrl, message_id=message_id, **kwargs)
                except Exception as exc:
                    getattr(_logger, log_level)("%s error: %s", func.__name__, exc, exc_info=True)
                    return default_return

            return async_wrapper

        @wraps(func)
        def wrapper(*, message_id: str, **kwargs: Any) -> Any:
            try:
                ctrl = get_controller()
                if not ctrl.enabled:
                    return default_return
                return func(ctrl=ctrl, message_id=message_id, **kwargs)
            except Exception as exc:
                getattr(_logger, log_level)("%s error: %s", func.__name__, exc, exc_info=True)
                return default_return

        return wrapper

    return decorator


def on_feishu_normalize(
    *,
    message_id: str,
    source: Any,
    event: Any,
    reply_anchor_id: str | None = None,
) -> None:
    """[注入点 0] _handle_message source 赋值后 — 修正飞书引用消息的虚假 thread_id."""
    try:
        ctrl = get_controller()
        if not ctrl.enabled:
            return

        platform = getattr(getattr(source, "platform", None), "value", "")
        if platform != "feishu":
            return

        raw = getattr(event, "raw_message", None)
        raw_event = raw.get("event") if isinstance(raw, dict) else None
        if raw_event is None:
            raw_event = getattr(raw, "event", None)

        raw_message = None
        if isinstance(raw_event, dict):
            raw_message = raw_event.get("message")
        elif raw_event is not None:
            raw_message = getattr(raw_event, "message", None)
        if raw_message is None and isinstance(raw, dict):
            raw_message = raw.get("message")
        if raw_message is None:
            raw_message = raw

        real_thread_id = None
        if isinstance(raw_message, dict):
            real_thread_id = raw_message.get("thread_id")
        else:
            real_thread_id = getattr(raw_message, "thread_id", None)

        reply_to = getattr(event, "reply_to_message_id", None)
        source_thread_id = getattr(source, "thread_id", None)

        _logger.info(
            "feishu inbound ids: msg=%s anchor=%s source_thread=%s raw_thread=%s reply_to=%s",
            message_id,
            reply_anchor_id,
            source_thread_id,
            real_thread_id,
            reply_to,
        )

        if reply_to and source_thread_id and not real_thread_id:
            source.thread_id = None
            event.source = source
    except Exception as exc:
        _logger.warning("on_feishu_normalize error: %s", exc, exc_info=True)


@_safe_hook()
def on_message_started(
    *,
    ctrl: Any,
    message_id: str | None = None,
    chat_id: str,
    chat_type: str = "",
    anchor_id: str | None = None,
    session_key: str | None = None,
) -> str | None:
    """[注入点 1] 函数开头 — message.started.

    返回实际创建的 message_id（无 id 的合成轮次为 ``syn-*``），供排队续轮把
    卡片归属传给完成钩子（#35）；未建卡时返回 None。
    """
    started: str | None = ctrl.on_message_started(
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        anchor_id=anchor_id,
        session_key=session_key,
    )
    return started


@_safe_hook()
def on_queued_followup_card_ready(
    *,
    ctrl: Any,
    message_id: str | None = None,
    chat_id: str,
    chat_type: str = "",
    session_key: str | None = None,
) -> str | None:
    """[注入点 1c] 续轮开始前先把该轮的卡建好，收尾钩子复用同一张（#37 重复投递修法）.

    续轮在流式阶段没有卡片时，Hermes 会把回复按纯文本流式投出去；等续轮结束再补卡
    就成了同一段文字两条投递。这里在续轮递归开始前建卡（无消息 id 落合成卡，按
    session_key 注册），流式直接进卡，核心那条纯文本路径不再触发。

    已存在则复用（事件型续轮在消息到达时就可能已有卡），无卡可建时返回 None，
    调用方据此回落原有路径。
    """
    existing: str | None = ctrl.resolve_session_id(message_id, session_key)
    if existing:
        return existing
    started: str | None = on_message_started(
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        anchor_id=None,
        session_key=session_key,
    )
    return started


def collect_stream_stats(gateway: Any, session_key: str | None) -> dict[str, Any]:
    """Best-effort 采集会话级缓存命中与生成速度指标，供完成卡片 footer 展示.

    数据取自 gateway 持有的活跃 agent（_running_agents 优先，其次 _agent_cache，
    后者为 tuple 包装）。口径与 Hermes 自身一致：
    - 缓存读取: ``agent.session_cache_read_tokens``（会话累计；命中率 =
      cache_read / prompt_tokens，prompt 已含缓存，见 agent/turn_usage.py 的归一化）；
    - 速度: ``_api_latency_history`` / ``_api_output_history`` 滚动近 10 次 API 调用的
      sum(output)/sum(latency) 纯生成吞吐（同 CLI 状态栏，剔除工具执行时间）。

    任一数据不可得即省略对应键；agent 不在缓存（被逐出/非方法作用域）返回 {}，
    footer 渲染端对缺失字段自动隐藏。
    """
    if gateway is None or not session_key:
        return {}
    agent: Any = None
    try:
        running = getattr(gateway, "_running_agents", None)
        if isinstance(running, dict):
            agent = running.get(session_key)
        if agent is None:
            cache = getattr(gateway, "_agent_cache", None)
            if cache is not None:
                entry = cache.get(session_key)
                agent = (entry[0] if entry else None) if isinstance(entry, tuple) else entry
    except Exception:
        return {}
    if agent is None:
        return {}

    stats: dict[str, Any] = {}
    try:
        cache_read = int(getattr(agent, "session_cache_read_tokens", 0) or 0)
        if cache_read > 0:
            stats["cache_read_tokens"] = cache_read
    except Exception:
        pass
    try:
        lhist = list(getattr(agent, "_api_latency_history", None) or [])
        ohist = list(getattr(agent, "_api_output_history", None) or [])
        n = min(len(lhist), len(ohist))  # 两者同步 append，取尾对齐（同 cli_status_bar_mixin）
        if n:
            latencies = [
                float(x) for x in lhist[-n:]
                if isinstance(x, (int, float)) and math.isfinite(float(x))
            ]
            total_lat = sum(latencies)
            total_out = sum(int(x) for x in ohist[-n:] if isinstance(x, (int, float)))
            if total_lat > 0 and total_out > 0:
                stats["tokens_per_sec"] = total_out / total_lat
    except Exception:
        pass
    return stats


@_safe_hook(default_return=False)
async def on_message_completed_wait(
    *,
    ctrl: Any,
    message_id: str,
    answer: str = "",
    is_error: bool = False,
    duration: float = 0.0,
    model: str = "",
    tokens: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    session_key: str | None = None,
    answer_rewritten: bool = False,
) -> bool:
    """[注入点 2] return 前 — message.completed，等待卡片完成收尾.

    ``answer_rewritten`` = 本轮答案被收尾闸门打回并重写（网关按本轮消息的
    finish_reason=verify_hook_continue 判定）：流式段里的旧稿要用定稿整段替换。
    """
    return bool(
        await ctrl.on_completed_wait(
            message_id=message_id,
            answer=answer,
            is_error=is_error,
            duration=duration,
            model=model,
            tokens=tokens,
            context=context,
            session_key=session_key,
            answer_rewritten=answer_rewritten,
        )
    )


@_safe_hook(default_return=False)
def on_message_needs_text_fallback(*, ctrl: Any, message_id: str) -> bool:
    """Return True once when CardKit failed and gateway must send plain text."""
    return bool(ctrl.consume_text_fallback(message_id))


@_safe_hook(default_return=False)
async def on_queued_followup_boundary(
    *,
    ctrl: Any,
    message_id: str,
    result: dict[str, Any],
    session_key: str | None = None,
    stream_stats: dict[str, Any] | None = None,
) -> bool:
    """Complete the current card before Hermes drains a queued follow-up turn."""
    if not isinstance(result, dict) or result.get("interrupted"):
        return False

    sent = bool(
        await ctrl.on_completed_wait(
            message_id=message_id,
            answer=result.get("final_response") or "",
            is_error=bool(result.get("failed")),
            duration=0.0,
            model=result.get("model", ""),
            tokens={
                "input_tokens": result.get("input_tokens", 0),
                "output_tokens": result.get("output_tokens", 0),
                **(stream_stats or {}),
            },
            context={
                "used_tokens": result.get("last_prompt_tokens", 0),
                "max_tokens": result.get("context_length", 0),
            },
            session_key=session_key,
        )
    )
    if sent:
        result["response_previewed"] = True
        result["already_sent"] = True
        result["final_response"] = ""
    else:
        ctrl.consume_text_fallback(message_id)
    return sent


@_safe_hook()
def on_queued_followup_result(*, ctrl: Any, message_id: str, followup_result: dict[str, Any]) -> None:
    """Carry the deepest queued follow-up id back to the outer completion hook."""
    if isinstance(followup_result, dict) and message_id:
        followup_result.setdefault("_hermes_lark_completion_id", message_id)


@_safe_hook(default_return=False)
async def on_queued_followup_completed(
    *,
    ctrl: Any,
    message_id: str,
    result: dict[str, Any],
    session_key: str | None = None,
    stream_stats: dict[str, Any] | None = None,
) -> bool:
    """[注入点 1b] 排队续轮收尾 — 封该轮的卡并抑制纯文本投递（#35）。

    续轮由 ``_run_agent_queued_followup`` 递归 ``_run_agent`` 执行，不经过
    ``_handle_message_with_agent``，因此本轮的 COMPLETE 钩子不会触发：卡片既没被
    封（用户看到「处理中」），回复又按 Hermes 默认路径发成纯文本。这里补上收尾。

    与 ``on_queued_followup_boundary`` 的区别：boundary 封的是**上一轮**的卡（续轮
    开始前），本钩子封的是**续轮自己**的卡（续轮结束后）。
    """
    if not isinstance(result, dict) or result.get("interrupted"):
        return False

    sent = bool(
        await ctrl.on_completed_wait(
            message_id=message_id,
            answer=result.get("final_response") or "",
            is_error=bool(result.get("failed")),
            duration=0.0,
            model=result.get("model", ""),
            tokens={
                "input_tokens": result.get("input_tokens", 0),
                "output_tokens": result.get("output_tokens", 0),
                **(stream_stats or {}),
            },
            context={
                "used_tokens": result.get("last_prompt_tokens", 0),
                "max_tokens": result.get("context_length", 0),
            },
            session_key=session_key,
        )
    )
    if sent:
        result["response_previewed"] = True
        result["already_sent"] = True
        result["final_response"] = ""
    else:
        ctrl.consume_text_fallback(message_id)
    return sent


@_safe_hook(default_return=False)
def on_tool_updated(
    *,
    ctrl: Any,
    message_id: str,
    tool_name: str,
    status: str,
    detail: str = "",
    session_key: str | None = None,
) -> bool:
    """[注入点 3] progress_callback — tool.updated."""
    return bool(
        ctrl.on_tool_update(
            message_id=message_id,
            tool_name=tool_name,
            status=status,
            detail=detail,
            session_key=session_key,
        )
    )


@_safe_hook(default_return=False, log_level="debug")
def on_answer_delta(*, ctrl: Any, message_id: str, text: str, session_key: str | None = None) -> bool:
    """[注入点 4] _stream_delta_cb — answer.delta."""
    return bool(ctrl.on_answer(message_id=message_id, text=text, session_key=session_key))


def mark_stream_delivered(consumer: Any, message_id: str | None) -> None:
    """把「最终回复已由 fry 卡片流式投递」告知网关的流消费者（#43 反馈）.

    答案增量被插件接走后，Hermes 的 stream consumer 全程空转，
    ``_run_agent_mark_streamed_delivery`` 判 ``streamed=False`` 打
    "Normal final-send NOT suppressed" 重复投递警告。这里给消费者挂
    ``has_durably_delivered_text`` 谓词（0.21+ 抑制判定的官方查询点，
    ``getattr`` 鸭子类型读取）：按 fry 卡片的健康状态回答。旧版 mono
    Hermes 只查 ``final_response_sent``（不可伪造，会触发 stale-finalize
    改写路径），不去碰——mono 侧本无该警告，最终投递由 already_sent 抑制。
    """
    if consumer is None:
        return
    if getattr(consumer, "_fry_delivery_predicate", None) is None:
        def _delivered(_final_text: str) -> bool:
            try:
                ctrl = get_controller()
            except Exception:
                return False
            return bool(ctrl.stream_card_healthy(getattr(consumer, "_fry_msg_id", None)))

        consumer._fry_delivery_predicate = _delivered
        consumer.has_durably_delivered_text = _delivered
    # 重定向换卡后 event_message_id 变化：每次增量都刷新归属
    consumer._fry_msg_id = message_id


@_safe_hook(default_return=False, log_level="debug")
def on_thinking_delta(*, ctrl: Any, message_id: str, text: str, session_key: str | None = None) -> bool:
    """[注入点 5] _interim_assistant_cb — thinking.delta."""
    return bool(ctrl.on_thinking(message_id=message_id, text=text, session_key=session_key))


@_safe_hook(default_return=False, log_level="debug")
def on_reasoning_delta(*, ctrl: Any, message_id: str, text: str, session_key: str | None = None) -> bool:
    """[注入点 6] reasoning_callback — native model reasoning delta."""
    return bool(ctrl.on_reasoning(message_id=message_id, text=text, session_key=session_key))


@_safe_hook(default_return=False, log_level="debug")
def on_background_review_message(
    *,
    ctrl: Any,
    message_id: str,
    text: str,
    sender: Callable[[str], Any],
) -> bool:
    """[注入点 7] background_review_callback — background.review."""
    deferred: bool = ctrl.defer_background_review(message_id=message_id, text=text, sender=sender)
    return deferred


@_safe_hook()
def on_message_aborted(*, ctrl: Any, message_id: str) -> None:
    """[注入点 8] stale return None 前 — message.aborted."""
    ctrl.on_aborted(message_id=message_id)


async def on_session_aborted(*, session_key: str) -> bool:
    """Terminate the active card after Hermes handles a busy-session /stop."""
    try:
        ctrl = get_controller()
        if not ctrl.enabled:
            return False
        return bool(await ctrl.on_session_aborted(session_key=session_key))
    except Exception as exc:
        _logger.warning("on_session_aborted error: %s", exc, exc_info=True)
        return False


@_safe_hook()
def on_message_interrupted(
    *,
    ctrl: Any,
    message_id: str,
    new_message_id: str,
    chat_id: str,
    chat_type: str = "",
    anchor_id: str | None = None,
    session_key: str | None = None,
) -> None:
    """[注入点 9] interrupt 发生 — message.interrupted."""
    ctrl.on_interrupted(
        old_message_id=message_id,
        new_message_id=new_message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        anchor_id=anchor_id,
        session_key=session_key,
    )


def on_busy_ack(
    *,
    ctrl: Any = None,
    message_id: str | None = None,
    session_key: str | None = None,
    text: str = "",
    **kwargs: Any,
) -> bool:
    """[注入点 12] 忙碌回执 — 写进当前卡的通知槽并吞掉纯文本（#43）.

    ``text`` 是 Hermes 自己合成的那条文案，插件原样落卡，不另写。
    返回 True 时注入点应跳过 Hermes 的纯文本发送。
    """
    try:
        ctrl = ctrl or get_controller()
        if not ctrl.enabled:
            return False
        return bool(ctrl.on_busy_ack(text=text, message_id=message_id, session_key=session_key))
    except Exception as exc:
        _logger.warning("on_busy_ack error: %s", exc, exc_info=True)
        return False


def on_turn_redirected(
    *,
    ctrl: Any = None,
    old_message_id: str | None = None,
    new_message_id: str | None = None,
    chat_id: str = "",
    chat_type: str = "",
    anchor_id: str | None = None,
    session_key: str | None = None,
    redirected: bool = False,
    text: str = "",
    message_id: str | None = None,
    **kwargs: Any,
) -> None:
    """[注入点 13] 忙碌提示入卡 — 重定向时换卡，其余模式并入当前卡（#43）.

    ``text`` 是 Hermes 合成的提示原文（重定向 / queue / steer / interrupt 共用
    一条回执通道）。只有在 ``redirected`` 为真或显式重定向时才走换卡：Hermes 已把回复
    重新锚定到新消息，卡片跟着旧卡收尾、新卡接手；新旧卡接续沿用现有
    ``on_interrupted`` + ``_interrupt_map``，完成回调仍按映射落到新卡。
    其余模式只把提示写进当前卡的通知槽。
    支持 ``message_id`` 别名与缺省 ``chat_id``，兼容镜像烘入的不同模板。
    """
    try:
        ctrl = ctrl or get_controller()
        if not ctrl.enabled:
            return
        old_session = ctrl._resolve_session(None, session_key) if session_key else None
        old_msg = old_message_id or (old_session.message_id if old_session else message_id) or ""
        new_msg = new_message_id or message_id or (old_session.message_id if old_session else "")
        target_chat = chat_id or (old_session.chat_id if old_session else "")
        is_redirect = redirected or (bool(new_msg and old_msg and new_msg != old_msg)) or bool(message_id and not text)

        if is_redirect:
            ctrl.on_interrupted(
                old_message_id=old_msg,
                new_message_id=new_msg,
                chat_id=target_chat,
                chat_type=chat_type,
                anchor_id=anchor_id,
                session_key=session_key,
            )
            if text.strip():
                ctrl.on_redirected_notice(text=text, message_id=new_msg, session_key=session_key)
            return
        if text.strip():
            ctrl.on_busy_ack(text=text, message_id=old_msg or None, session_key=session_key)
    except Exception as exc:
        _logger.warning("on_turn_redirected error: %s", exc, exc_info=True)


def on_session_compressed(
    *,
    ctrl: Any = None,
    session_key: str | None = None,
    message_id: str | None = None,
    **kwargs: Any,
) -> bool:
    """[注入点 14] 上下文压缩触发 — 封印当前会话的活跃卡片."""
    try:
        ctrl = ctrl or get_controller()
        if not ctrl.enabled:
            return False
        return bool(ctrl.on_compression(session_key=session_key, message_id=message_id))
    except Exception as exc:
        _logger.warning("on_session_compressed error: %s", exc, exc_info=True)
        return False


def on_cron_deliver(
    *,
    chat_id: str,
    content: str,
    loop: Any = None,
    task_name: str = "",
    run_time: str = "",
) -> bool:
    """[注入点 10] cron 推送 — 包装为飞书卡片发送."""
    try:
        ctrl = get_controller()
        if not ctrl.enabled:
            return False
        # 头部时间取投递时刻：注入点传入的是 job.next_run_at（下一次计划时刻，
        # 永远是未来时间）；last_run_at 在 _record_run_outcome 时才写入，投递时还是上一轮的值。
        run_time = datetime.now().astimezone().isoformat()
        return bool(ctrl.on_cron_deliver(
            chat_id=chat_id, content=content, loop=loop,
            task_name=task_name, run_time=run_time,
        ))
    except Exception as exc:
        _logger.warning("on_cron_deliver error: %s", exc, exc_info=True)
        return False


async def on_background_deliver(
    *,
    chat_id: str,
    preview: str,
    content: str,
    reply_to_message_id: str | None = None,
) -> bool:
    """[注入点 11] background 任务完成推送 — 包装为飞书卡片发送."""
    try:
        ctrl = get_controller()
        if not ctrl.enabled:
            return False
        return await ctrl.on_background_deliver(
            chat_id=chat_id,
            preview=preview,
            content=content,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as exc:
        _logger.warning("on_background_deliver error: %s", exc, exc_info=True)
        return False


@_safe_hook()
def on_clarify_enter(
    *,
    ctrl: Any,
    message_id: str,
    chat_id: str | None = None,
    session_key: str | None = None,
) -> None:
    """[注入点 12] clarify_callback 进入 — 暂停 flush 保留当前卡。"""
    ctrl.on_clarify_enter(message_id=message_id, chat_id=chat_id, session_key=session_key)


@_safe_hook()
def on_clarify_exit(
    *,
    ctrl: Any,
    message_id: str,
    chat_id: str | None = None,
    session_key: str | None = None,
) -> None:
    """[注入点 12] clarify_callback 退出 — 标记待封卡，等 tool.completed 触发切卡。"""
    ctrl.on_clarify_exit(message_id=message_id, chat_id=chat_id, session_key=session_key)
