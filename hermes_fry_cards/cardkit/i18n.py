"""飞书卡片 i18n — 中英双语文本映射."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..config import Config, hermes_home

__all__ = [
    "_LOCALES",
    "_T",
    "_content_lang",
    "_content_t",
    "_i18n",
    "_t",
]

_LOCALES = ["zh_cn", "en_us"]

_T: dict[str, tuple[str, str]] = {
    "status_completed": ("✅ Completed", "✅ 已完成"),
    "status_error": ("❌ Error", "❌ 出错"),
    "status_stopped": ("🛑 Stopped", "🛑 已停止"),
    "elapsed": ("Elapsed {}", "耗时 {}"),
    "context": ("Context {}", "上下文 {}"),
    "cache_hit": ("Cache hit {}%", "缓存命中 {}%"),
    "processing": ("Processing...", "处理中..."),
    "processing_prefix": ("💭 Processing...", "💭 处理中..."),
    "processing_prefix_plain": ("Processing...", "处理中..."),
    "status_completed_plain": ("Completed", "已完成"),
    "status_error_plain": ("Error", "出错"),
    "status_stopped_plain": ("Stopped", "已停止"),
    "tool_use": ("Tool use", "工具执行"),
    "tool_running": ("Tool running", "工具执行中"),
    "tool_omitted": ("… and {} earlier tool steps", "…另有 {} 步工具调用"),
    "tool_pending": ("🛠️ Tool use pending", "🛠️ 等待工具执行"),
    "process_panel": ("💭 Thinking process", "💭 思考过程"),
    "thought_rounds": ("{} thinking stages", "思考了 {} 轮"),
    "steps": ("{} step{}", "{} 步"),
    "stages_short": ("{} stage{}", "{} 段"),
    "thought": ("Thought", "思考"),
    "thinking_panel": ("Thinking", "思考中"),
    "thinking_running": ("💭 Thinking", "💭 思考中"),
    "reasoning_count": ("💭{0}", "💭{0}"),
    "tool_count": ("🔧{0}", "🔧{0}"),
    "process_counts_compact": ("💭{0}·🔧{1}", "💭{0}·🔧{1}"),
    "reasoning_count_streaming": ("💭 Thinking {0} stages", "💭 已思考 {0} 轮"),
    "tool_count_streaming": ("🔧 Tools {0} steps", "🔧 工具执行中 {0} 步"),
    "tool_totals_summary": ("🔧 Tools executed: {0} steps · Elapsed {1}", "🔧 工具执行 {0} 步 · 耗时 {1}"),
    "thought_for": ("Thought for {}", "思考了 {}"),
    "done": ("Done.", "完成。"),
    "continued": ("This turn continues in the next card.", "本轮继续。正文见后续卡片。"),
    "no_details": ("No interim messages in this turn.", "本轮没有中途消息"),
    # 有思考过程却没有正文：回合被截断（服务重启、上游中断），不是「做完了没话说」。
    # 这类卡原先落一行 Done.，用户读成「回复只有一个 Done」，实际是内容没送达。
    "interrupted_no_answer": (
        "This turn was cut off before the reply was written; nothing was delivered. Send the request again.",
        "本轮在正文写出之前被打断，内容没有送达。请重新发起。",
    ),
    # 通知区（#43）：提示渲染 Hermes 原文，不再套「💡 提示」标签（#43 反馈），
    # 下列词条仅供外部引用/排查保留。
    "notice": ("Notice", "提示"),
    "notice_redirect": ("Redirected", "已重定向"),
    "notice_background": ("Background task", "后台任务"),
    "notice_gateway_restart": ("Gateway restarted", "网关重启"),
    "card_overflow_fallback": (
        "This reply exceeded the Feishu card element limit; the full answer is delivered as plain text below.",
        "本次回复超出飞书卡片元素上限，完整内容以纯文本在下方送达。",
    ),
    # 内容层提示语（markdown 正文内嵌，无法走 i18n_content 双语 dict），
    # 由 _content_t 按 streaming.content_lang 构建时选取。
    "table_overflow_notice": (
        "The following tables have been converted to compact field lists to fit Feishu card limits; content is fully preserved.",
        "后续表格已转换为紧凑字段列表，以兼容飞书卡片限制；内容完整保留。",
    ),
    "clamp_truncated": (
        "… (content truncated to prevent card overflow) …",
        "…（内容过长已截断，防卡片溢出）…",
    ),
    "clamp_middle_omitted": (
        "… (middle section omitted; opening context and final conclusions preserved, to prevent card overflow) …",
        "…（中段分析已省略，保留开头背景与末尾核心结论，防卡片溢出）…",
    ),
}


def _i18n(en: str, zh: str) -> dict[str, str]:
    return {"zh_cn": zh, "en_us": en}


def _t(key: str) -> dict[str, str]:
    """简写: _t("processing") → _i18n(*_T["processing"])。"""
    return _i18n(*_T[key])


# 显式覆盖入口供现有测试使用；运行时语言每次经 Config 热读。
_CONTENT_LANG_CACHE: str | None = None


@lru_cache(maxsize=16)
def _content_config(home: Path) -> Config:
    """按 profile 复用配置实例及其元数据缓存."""
    return Config(home)


def _content_lang() -> str:
    """读取当前 profile 的内容语言，复用 Config 的安全重读路径."""
    if _CONTENT_LANG_CACHE is not None:
        return _CONTENT_LANG_CACHE
    from .styles import _style_config

    config = _style_config.get()
    return config.content_lang if config is not None else _content_config(hermes_home()).content_lang


def _content_t(key: str, *, lang: str | None = None) -> str:
    """内容层单语词条；可传入本次渲染已解析的语言以保持缓存一致."""
    if key not in _T:
        return key  # 非词条（如 Hermes 原文）原样返回，由调用方负责语言选取
    en, zh = _T[key]
    return en if (lang if lang is not None else _content_lang()) == "en" else zh
