"""Collapse routine context-compaction status lines into one card per turn.

The core emits up to three status lines while compacting (start, heartbeat, done).
They arrive over the plain status channel, so the Feishu adapter would post each
as its own text bubble. Grouping them keeps one card whose text is the current
line, so a finished compaction shows its result and nothing above it.

A compression that a pre-check starts announces itself first with the ``📦 Pre-API
compression: …`` or ``📦 Preflight compression: …`` line. It opens the same card, so
that line lands in the card instead of becoming a separate bubble above it.
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
HEARTBEAT_MARKERS = ("still summarizing",)
DONE_MARKERS = ("Context compaction complete",)
RESULT_MARKERS = ("Approx request size:",)
# Announced ahead of the compaction by the pre-API check (mid-turn) or the turn-start
# threshold check; the start line that follows belongs to the same compression.
PRE_ANNOUNCE_MARKERS = ("Pre-API compression", "Preflight compression")

_MAX_TRACKED = 64
_TITLE_EN = "Context compaction"
_TITLE_ZH = "上下文压缩"


def classify(content: str) -> str | None:
    """Return ``prestart`` / ``start`` / ``heartbeat`` / ``done`` / ``result`` for a compaction line, else ``None``.

    ``prestart`` is the announcement that opens the card for the compaction that
    follows it (pre-API or turn-start preflight). ``result`` is the summary the
    manual ``/compress`` command answers with (``Compressed: 232 -> 41 messages``
    plus the request-size line). It belongs in the same card as the progress lines,
    not in a second bubble below them.
    """
    text = str(content or "")
    if any(marker in text for marker in DONE_MARKERS):
        return "done"
    if any(marker in text for marker in HEARTBEAT_MARKERS):
        return "heartbeat"
    if any(marker in text for marker in START_MARKERS):
        return "start"
    if any(marker in text for marker in PRE_ANNOUNCE_MARKERS):
        return "prestart"
    if "Compressed" in text and any(marker in text for marker in RESULT_MARKERS):
        return "result"
    return None


def build_compaction_card(lines: list[str], config: Config) -> dict[str, Any]:
    """One card carrying the current compaction line (the manual result uses two)."""
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

        Grouping follows the wording alone: the pre-API line opens a card, the
        start line of that same compaction overwrites it, heartbeat lines
        overwrite it in place, the done line closes it, and the manual /compress
        result overwrites the done line. A start or pre-API line that finds a card
        opened by anything else closes that card and opens a new one, so a
        compaction that never reported completion cannot swallow the next turn.
        The one exception is the in-flight window: the announcement joins a start
        card that has not been posted yet, since that is the same compression.
        """
        kind = classify(content)
        if kind is None:
            return None
        now = time.monotonic()
        entry = self._entries.get(str(chat_id))
        if entry is not None and entry["final"] and kind != "result":
            entry = None
        if kind in {"start", "prestart"} and entry is not None:
            # The pre-API announcement and the start line of the compaction it
            # announced are one compression: both stay in the card it opened.
            same_compression = entry.get("opened_by") == "prestart"
            if (not same_compression and kind == "prestart"
                    and entry.get("opened_by") == "start"
                    and entry.get("message_id") is None):
                # The two lines can be dispatched at once. If the announcement
                # reaches the card before the start line's card is posted, it
                # joins that card instead of opening a second one beside it.
                same_compression = True
            if not same_compression:
                entry["final"] = True
                entry = None
        if entry is None:
            entry = {"message_id": None, "lines": [], "final": False, "updated": now,
                     "opened_by": kind}
            self._entries[str(chat_id)] = entry
            self._prune()
        # The card shows the current line only: each new line overwrites the previous one,
        # so a finished compaction keeps the result (or the done line) and nothing above it.
        entry["lines"] = [line for line in str(content).strip().splitlines() if line.strip()]
        entry["updated"] = now
        entry["final"] = kind in {"done", "result"}
        return entry

    def bind(self, chat_id: str, entry: dict[str, Any], message_id: str) -> None:
        entry["message_id"] = str(message_id)
        self._entries[str(chat_id)] = entry

    def reset(self, chat_id: str) -> None:
        self._entries.pop(str(chat_id), None)

    def _prune(self) -> None:
        # Closed entries stay until the next line replaces them: the manual
        # /compress result arrives after the done line and must land in the same card.
        while len(self._entries) > _MAX_TRACKED:
            self._entries.popitem(last=False)
