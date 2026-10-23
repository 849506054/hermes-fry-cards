"""Integration coverage for the Hermes 0.21+ split gateway layout."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from hermes_fry_cards.patcher import (
    MARKERS,
    MK_CRON_DELIVER,
    MK_FOLLOWUP_COMPLETE,
    MK_FOLLOWUP_COMPLETE_END,
    MK_FOLLOWUP_DONE,
    MK_GROUP_BOUNDARY,
    MK_GROUP_BOUNDARY_END,
    CronPatcher,
    Patcher,
    PatcherError,
)

MODULAR_SOURCES = {
    "inbound": """
        async def _handle_message(self, event):
            _admitted = await self._admit(event)
            event, source, is_internal = _admitted
            return source
    """,
    "turn": """
        async def _handle_message_with_agent(self, source, event, hooks, adapter, response):
            agent_result = {"failed": False}
            self._hmwa_discard_stale_result(agent_result)
            # Restart the typing indicator; the outer typing task may be stale
            pending_event = None
            next_message_id = None
            next_source = source
            images, text_content = adapter.extract_images(response)
            hooks.emit("agent:end", agent_result)
            _footer_line = self._hmwa_runtime_footer_line(agent_result)
            return response

        async def _run_agent_queued_followup(self, turn_ctx, result, response):
            if not result.get("interrupted"):
                response = result
            followup_result = result
            return _preserve_queued_followup_history_offset(result, followup_result)

        async def _run_agent_queued_followup_0211(self, turn_ctx, result, response, next_inbound_id):
            followup_result = await self._run_agent(
                message=next_message, context_prompt=turn_ctx.context_prompt, history=updated_history,
            )
            merged = _preserve_queued_followup_history_offset(result, followup_result)
            # 0.21.1: terminal turn ledger identity
            if isinstance(merged, dict) and "queued_terminal_inbound_id" not in merged:
                merged = {**merged, "queued_terminal_inbound_id": next_inbound_id}
            return merged
    """,
    "runner": """
        def configure(self, agent, reasoning_config):
            agent.reasoning_config, agent.service_tier = reasoning_config, None
            agent.background_review_callback, bg_release = None, None
            agent.clarify_callback = self._clarify_callback_sync

        def progress_callback(event_type, tool_name=None, preview=None):
            return event_type

        def interim_assistant_cb(text):
            return text

        def stream_delta_cb(text):
            return text

        def _combined_ephemeral_prompt(self):
            ctx = self._ctx
            combined = ctx.context_prompt or ""
            for extra in ((ctx.channel_prompt or "").strip(),):
                if extra:
                    combined = (combined + "\\n\\n" + extra).strip()
            return combined

        def _sync_session_after_run(self, agent_history):
            if session_was_split:
                logger.info("Session split detected: %s → %s (compression)", ctx.session_id, agent_session_id)
            return compacted_in_place, agent_session_id, offset
    """,
    "busy": """
        async def stop(self, session_key):
            await self._interrupt_and_clear_session(
                session_key,
                invalidation_reason="stop_command",
            )

        async def _handle_active_session_busy_message(self, event, session_key):
            message = "Queued for the next turn"
            try:
                await adapter._send_with_retry(
                    chat_id=event.source.chat_id,
                    content=message,
                )
            except Exception:
                pass
            return True
    """,
}

# Hermes 0.22 微模块重构（#38）：progress_callback 从 run_turn_runner.py 迁移至独立的
# run_turn_progress.py，hooks.emit("agent:end") 从 run_turn.py 迁移至 run_turn_persistence.py。
# runner 里没有 progress_callback、turn 里没有 agent:end —— 锚点必须跟着迁到新模块。
MODULAR_022_OVERRIDES = {
    "turn": """
        async def _handle_message_with_agent(self, source, event, hooks, adapter, response):
            agent_result = {"failed": False}
            self._hmwa_discard_stale_result(agent_result)
            # Restart the typing indicator; the outer typing task may be stale
            pending_event = None
            next_message_id = None
            next_source = source
            images, text_content = adapter.extract_images(response)
            _footer_line = self._hmwa_runtime_footer_line(agent_result)
            return response

        async def _run_agent_queued_followup(self, turn_ctx, result, response):
            if not result.get("interrupted"):
                response = result
            followup_result = result
            return _preserve_queued_followup_history_offset(result, followup_result)
    """,
    "runner": """
        def configure(self, agent, reasoning_config):
            agent.reasoning_config, agent.service_tier = reasoning_config, None
            agent.background_review_callback, bg_release = None, None
            agent.clarify_callback = self._clarify_callback_sync

        def interim_assistant_cb(text):
            return text

        def stream_delta_cb(text):
            return text

        def _combined_ephemeral_prompt(self):
            ctx = self._ctx
            combined = ctx.context_prompt or ""
            for extra in ((ctx.channel_prompt or "").strip(),):
                if extra:
                    combined = (combined + "\\n\\n" + extra).strip()
            return combined

        def _sync_session_after_run(self, agent_history):
            if session_was_split:
                logger.info("Session split detected: %s → %s (compression)", ctx.session_id, agent_session_id)
            return compacted_in_place, agent_session_id, offset
    """,
}

PROGRESS_SOURCE_022 = """
    class TurnProgressMixin:
        def progress_callback(event_type, tool_name=None, preview=None):
            return event_type
"""

PERSISTENCE_SOURCE_022 = """
    class TurnPersistenceMixin:
        async def persist_turn(self, hooks, agent_result):
            hooks.emit("agent:end", agent_result)
            return agent_result
"""


@pytest.fixture()
def modular_022_copy(tmp_path: Path) -> dict[str, Path]:
    """0.21 基线布局 + progress/persistence 微模块：tool 与 agent:end 已迁移。"""
    copied: dict[str, Path] = {}
    sources = {**MODULAR_SOURCES, **MODULAR_022_OVERRIDES}
    for name, content in sources.items():
        destination = tmp_path / f"run_{name}.py"
        destination.write_text(textwrap.dedent(content), encoding="utf-8")
        copied[name] = destination
    progress = tmp_path / "run_turn_progress.py"
    progress.write_text(textwrap.dedent(PROGRESS_SOURCE_022), encoding="utf-8")
    copied["progress"] = progress
    persistence = tmp_path / "run_turn_persistence.py"
    persistence.write_text(textwrap.dedent(PERSISTENCE_SOURCE_022), encoding="utf-8")
    copied["persistence"] = persistence
    return copied


def test_modular_022_verify_accepts_migrated_anchors(modular_022_copy: dict[str, Path]) -> None:
    """#38: tool 锚点在 progress、agent:end 在 persistence 时 verify 必须通过。"""
    Patcher(modular_paths=modular_022_copy).verify_target()


def test_modular_022_verify_fails_without_any_host(modular_copy: dict[str, Path]) -> None:
    """runner 缺 progress_callback 且无 progress 模块时，verify 必须点名 tool 缺失。

    0.22 布局若被错误地只传 4 个基础模块（发现逻辑失效），不能静默通过。
    """
    broken = dict(modular_copy)
    content = broken["runner"].read_text(encoding="utf-8")
    broken["runner"].write_text(
        "\n".join(
            line for line in content.splitlines()
            if "progress_callback" not in line and "return event_type" not in line
        ) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(PatcherError, match="tool"):
        Patcher(modular_paths=broken).verify_target()


def test_modular_022_apply_installs_tool_hook_in_progress_module(modular_022_copy: dict[str, Path]) -> None:
    """TOOL 钩子必须落在 progress 模块，persistence 不写回；幂等与可逆同样成立。"""
    originals = {name: path.read_text(encoding="utf-8") for name, path in modular_022_copy.items()}
    patcher = Patcher(modular_paths=modular_022_copy)

    patcher.apply()
    assert patcher.is_fully_patched()

    contents = {name: path.read_text(encoding="utf-8") for name, path in modular_022_copy.items()}
    assert "HERMES_LARK_TOOL_BEGIN" in contents["progress"]
    assert "HERMES_LARK_TOOL_BEGIN" not in contents["runner"]
    assert "HERMES_LARK_NORMALIZE_BEGIN" in contents["inbound"]
    assert "HERMES_LARK_STOP_BEGIN" in contents["busy"]
    # #43：忙碌回执入卡 / 重定向换卡（可选锚点）
    assert "HERMES_LARK_BUSY_ACK_BEGIN" in contents["busy"]
    assert "HERMES_LARK_REDIRECT_BEGIN" in contents["busy"]
    for begin, _end in [*MARKERS, (MK_GROUP_BOUNDARY, MK_GROUP_BOUNDARY_END)]:
        total = sum(content.count(begin) for content in contents.values())
        assert total == 1, f"{begin} installed {total}x"
    for path in modular_022_copy.values():
        ast.parse(path.read_text(encoding="utf-8"))

    # persistence 没有注入点：apply 不写回它（内容与 progress 的注入互相独立）。
    assert contents["persistence"] == originals["persistence"]

    patcher.apply()
    assert contents == {name: p.read_text(encoding="utf-8") for name, p in modular_022_copy.items()}

    patcher.remove()
    assert originals == {name: p.read_text(encoding="utf-8") for name, p in modular_022_copy.items()}


def test_modular_paths_accepts_optional_and_rejects_unknown(tmp_path: Path) -> None:
    """modular_paths 允许可选模块（progress/persistence），拒绝拼写错误的模块名。"""
    base = {
        name: tmp_path / f"run_{name}.py"
        for name in ("inbound", "turn", "runner", "busy")
    }
    for path in base.values():
        path.write_text("# stub\n", encoding="utf-8")
    # 缺 progress/persistence 的 0.21 布局仍合法
    Patcher(modular_paths=dict(base))
    with pytest.raises(PatcherError, match="unknown modules"):
        Patcher(modular_paths={**base, "presistense": tmp_path / "typo.py"})
    with pytest.raises(PatcherError, match="at least"):
        missing = {k: v for k, v in base.items() if k != "busy"}
        Patcher(modular_paths=missing)


CRON_SOURCE = """
    def deliver(job, targets, cleaned_delivery_content, loop, adapters, config):
        delivery_errors = []
        for target in targets:
            t = prepare(target)
            if t is None:
                continue
            target_errors: list = []
            delivered = send(t, cleaned_delivery_content)
            if not delivered:
                fallback(t)
        return delivery_errors
"""


@pytest.fixture()
def modular_copy(tmp_path: Path) -> dict[str, Path]:
    copied = {}
    for name, content in MODULAR_SOURCES.items():
        destination = tmp_path / f"run_{name}.py"
        destination.write_text(textwrap.dedent(content), encoding="utf-8")
        copied[name] = destination
    return copied


def test_modular_apply_is_complete_idempotent_and_reversible(modular_copy: dict[str, Path]) -> None:
    originals = {name: path.read_text(encoding="utf-8") for name, path in modular_copy.items()}
    patcher = Patcher(modular_paths=modular_copy)

    patcher.verify_target()
    patcher.apply()
    assert patcher.is_fully_patched()

    combined = "\n".join(path.read_text(encoding="utf-8") for path in modular_copy.values())
    for begin, end in MARKERS:
        assert combined.count(begin) == 1
        assert combined.count(end) == 1
    for path in modular_copy.values():
        ast.parse(path.read_text(encoding="utf-8"))
        assert path.with_suffix(path.suffix + ".hermes_lark.bak").exists()

    first = {name: path.read_text(encoding="utf-8") for name, path in modular_copy.items()}
    patcher.apply()
    assert first == {name: path.read_text(encoding="utf-8") for name, path in modular_copy.items()}

    patcher.remove()
    assert originals == {name: path.read_text(encoding="utf-8") for name, path in modular_copy.items()}


def test_modular_marker_distribution(modular_copy: dict[str, Path]) -> None:
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    contents = {name: path.read_text(encoding="utf-8") for name, path in modular_copy.items()}

    assert "HERMES_LARK_NORMALIZE_BEGIN" in contents["inbound"]
    for marker in (
        "START", "COMPLETE", "FOLLOWUP_COMPLETE", "FOLLOWUP_RESULT", "FOLLOWUP_DONE",
        "ABORT", "INTERRUPT", "BG_DELIVER",
    ):
        assert f"HERMES_LARK_{marker}_BEGIN" in contents["turn"]
    for marker in ("TOOL", "ANSWER", "THINKING", "REASONING", "BACKGROUND_REVIEW", "CLARIFY", "COMPRESSION"):
        assert f"HERMES_LARK_{marker}_BEGIN" in contents["runner"]
    assert "HERMES_LARK_STOP_BEGIN" in contents["busy"]
    # #43：忙碌回执入卡 / 重定向换卡（可选锚点）
    assert "HERMES_LARK_BUSY_ACK_BEGIN" in contents["busy"]
    assert "HERMES_LARK_REDIRECT_BEGIN" in contents["busy"]


def test_modular_followup_done_seals_card_before_merge(modular_copy: dict[str, Path]) -> None:
    """#35: the queued follow-up's own card is sealed before the chain merges its result.

    The follow-up turn runs a recursive ``_run_agent`` that never re-enters
    ``_handle_message_with_agent``, so the COMPLETE hook never fires for it. The new hook
    therefore anchors on the ``_preserve_queued_followup_history_offset`` line and must land
    BEFORE it: ``followup_result`` is already bound above, but the merged value handed back
    to the outer handler is what carries ``already_sent`` and suppresses the plain text.
    """
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    lines = modular_copy["turn"].read_text(encoding="utf-8").splitlines()

    done = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_DONE in line)
    merge = next(
        i for i, line in enumerate(lines)
        if "_preserve_queued_followup_history_offset(result, followup_result)" in line
        and not line.strip().startswith("# HERMES_LARK")
    )
    assert done < merge, "FOLLOWUP_DONE must be injected before the merge line"

    body = "\n".join(lines[done:merge])
    assert "on_queued_followup_completed" in body, "hook must seal the follow-up turn's card"
    assert "already_sent" in body and "final_response" in body, "must suppress the plain-text send"
    # An event-less follow-up carries no inbound id; it must reuse the card the boundary
    # pre-created for this turn (falling back to creating one when there is none). (#37)
    assert "on_queued_followup_card_ready" in body and "message_id=None" in body


def test_modular_boundary_precreates_followup_card_before_recursion(modular_copy: dict[str, Path]) -> None:
    """#37: the boundary hook pre-creates the follow-up turn's card before the recursion.

    Without a card at stream time Hermes streams that turn's reply as plain text, and the
    completion hook then adds a card carrying the same text — the reply lands twice. The
    pre-create must land AFTER sealing the previous card and BEFORE the recursive
    ``_run_agent`` call, keyed by the follow-up's own session.
    """
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    lines = modular_copy["turn"].read_text(encoding="utf-8").splitlines()

    complete = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_COMPLETE in line)
    complete_end = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_COMPLETE_END in line)
    bound = next(i for i, line in enumerate(lines) if "followup_result = await self._run_agent(" in line)

    body = "\n".join(lines[complete:complete_end])
    assert "on_queued_followup_card_ready" in body, "boundary must pre-create the follow-up card"
    assert "on_queued_followup_boundary" in body, "boundary must still seal the previous card"
    assert body.index("on_queued_followup_boundary") < body.index("on_queued_followup_card_ready"), (
        "seal the previous card first, then pre-create this turn's"
    )
    assert complete < bound, "pre-create must land before the recursive _run_agent call"
    assert "self._session_key_for_source" in body, "pre-create must key the card by the follow-up's session"


def test_modular_cron_apply_is_idempotent_and_reversible(tmp_path: Path) -> None:
    copied = tmp_path / "scheduler_delivery.py"
    copied.write_text(textwrap.dedent(CRON_SOURCE), encoding="utf-8")
    original = copied.read_text(encoding="utf-8")
    patcher = CronPatcher(cron_path=copied)

    patcher.verify_target()
    patcher.apply()
    first = copied.read_text(encoding="utf-8")
    assert MK_CRON_DELIVER in first
    ast.parse(first)

    patcher.apply()
    assert copied.read_text(encoding="utf-8") == first
    patcher.remove()
    assert copied.read_text(encoding="utf-8") == original


def test_busy_ack_hook_merges_receipt_and_suppresses_plain_text(modular_copy: dict[str, Path]) -> None:
    """#43: 忙碌回执写入当前卡并吞掉 Hermes 纯文本（返回 True 即消费）。"""
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    busy = modular_copy["busy"].read_text(encoding="utf-8")

    begin = busy.index("HERMES_LARK_BUSY_ACK_BEGIN")
    end = busy.index("HERMES_LARK_BUSY_ACK_END")
    block = busy[begin:end]
    assert "from hermes_fry_cards.patch import on_busy_ack" in block
    assert "text=message" in block
    assert "return True" in block
    assert "injected hook failed: busy_ack" in block
    # 注入点落在回执发送语句之前（占位回执前的逻辑），且仍在 try 体内
    ack = busy.index("HERMES_LARK_BUSY_ACK_BEGIN")
    assert ack < busy.index("_send_with_retry(")
    assert busy.index("try:", busy.index("async def _handle_active_session_busy_message")) < ack


def test_redirect_hook_rebuilds_card_before_notice(modular_copy: dict[str, Path]) -> None:
    """#43: 重定向先经 on_interrupted 换卡，再写提示。"""
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    busy = modular_copy["busy"].read_text(encoding="utf-8")

    block = busy[busy.index("HERMES_LARK_REDIRECT_BEGIN"):busy.index("HERMES_LARK_REDIRECT_END")]
    assert "from hermes_fry_cards.patch import on_turn_redirected" in block
    assert "new_message_id=event.message_id" in block
    assert "injected hook failed: redirect" in block


def test_optional_hooks_are_skipped_when_anchor_missing(tmp_path: Path) -> None:
    """#43: 旧版 Hermes 的 busy 模块没有回执回调时，跳过注入而不是让安装失败。"""
    paths = {}
    for name, content in MODULAR_SOURCES.items():
        if name == "busy":
            content = """
        async def stop(self, session_key):
            await self._interrupt_and_clear_session(
                session_key,
                invalidation_reason="stop_command",
            )
    """
        destination = tmp_path / f"run_{name}.py"
        destination.write_text(textwrap.dedent(content), encoding="utf-8")
        paths[name] = destination

    patcher = Patcher(modular_paths=paths)
    patcher.verify_target()
    patcher.apply()

    busy = paths["busy"].read_text(encoding="utf-8")
    assert "HERMES_LARK_STOP_BEGIN" in busy
    assert "HERMES_LARK_BUSY_ACK_BEGIN" not in busy
    assert "HERMES_LARK_REDIRECT_BEGIN" not in busy
    assert patcher.is_fully_patched()
