"""Collapse routine context-compaction status lines into one card per turn.

The core emits up to three status lines while compacting (start, heartbeat, done).
They arrive over the plain status channel, so the Feishu adapter would post each
as its own text bubble. Grouping them keeps a single card that grows in place.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

from .cardkit.builder import _body_style, _build_body, build_notice_header
from .cardkit.styles import style_scope, text_element
from .config import Config

# Status lines the core emits around a compaction. Keep the phrases intact when
# the core rewording is picked up: they are the only signal that tells these
# apart from unrelated lifecycle statuses.
START_MARKERS = ("Compacting context",)
DONE_MARKERS = ("Context compaction complete",)

WINDOW_SECONDS = 600.0
_MAX_TRACKED = 64
_TITLE_EN = "Context compaction"
_TITLE_ZH = "上下文压缩"


def classify(content: str) -> str | None:
    """Return ``start`` / ``done`` for a compaction status line, else ``None``."""
    text = str(content or "")
    if any(marker in text for marker in DONE_MARKERS):
        return "done"
    if any(marker in text for marker in START_MARKERS):
        return "start"
    return None


def build_compaction_card(lines: list[str], config: Config) -> dict[str, Any]:
    """One card carrying every compaction line for this turn, in arrival order."""
    with style_scope(config):
        elements = [
            text_element(
                {"tag": "markdown", "content": line, "text_size": config.body_text_size},
                "body",
            )
            for line in lines
        ]
        for element, line in zip(elements, lines, strict=True):
            # Status text is authored upstream; decoration must not rewrite it.
            element["content"] = line
        card: dict[str, Any] = {
            "schema": "2.0",
            "config": {
                "width_mode": config.width_mode,
                "style": _body_style(config),
                "summary": {"content": lines[-1][:120].replace("\n", " ").strip()},
            },
            "body": _build_body(elements),
        }
        if config.header_enabled:
            card["header"] = build_notice_header(config=config)
        return card


class CompactionCards:
    """Track one open compaction card per chat, keyed by chat id."""

    def __init__(self) -> None:
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def take(self, chat_id: str, content: str) -> dict[str, Any] | None:
        """Resolve the card that should receive ``content``.

        Returns ``None`` when the line is not a compaction status. Otherwise the
        entry carries ``lines`` to render plus ``message_id`` when the card
        already exists; a fresh entry has no ``message_id`` yet.
        """
        kind = classify(content)
        if kind is None:
            return None
        now = time.monotonic()
        entry = self._entries.get(str(chat_id))
        if entry is not None and (entry["final"] or now - entry["updated"] > WINDOW_SECONDS):
            entry = None
        if entry is None:
            entry = {"message_id": None, "lines": [], "final": False, "updated": now}
            self._entries[str(chat_id)] = entry
            self._prune(now)
        entry["lines"].append(str(content).strip())
        entry["updated"] = now
        entry["final"] = kind == "done"
        return entry

    def bind(self, chat_id: str, entry: dict[str, Any], message_id: str) -> None:
        entry["message_id"] = str(message_id)
        self._entries[str(chat_id)] = entry

    def reset(self, chat_id: str) -> None:
        self._entries.pop(str(chat_id), None)

    def _prune(self, now: float) -> None:
        for key in [key for key, entry in self._entries.items()
                    if entry["final"] or now - entry["updated"] > WINDOW_SECONDS]:
            self._entries.pop(key, None)
        while len(self._entries) > _MAX_TRACKED:
            self._entries.popitem(last=False)
