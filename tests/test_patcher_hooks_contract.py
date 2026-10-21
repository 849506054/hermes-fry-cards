"""Executable contracts for injected current-anchor delivery and optional hosts."""

from __future__ import annotations

import ast
import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_fry_cards import patch as hooks
from hermes_fry_cards import patcher as injector


def _runner(body: str, parameters: str, suffix: str = ""):
    namespace = {}
    exec("async def run(" + parameters + "):\n" + body + suffix, namespace)
    return namespace["run"]


def test_start_preserves_raw_message_and_anchor_alias() -> None:
    event = SimpleNamespace(message_id="raw")
    source = SimpleNamespace(platform=SimpleNamespace(value="feishu"), chat_id="chat", chat_type="group")
    runner = MagicMock()
    runner._reply_anchor_for_event.return_value = "anchor"
    with patch.object(hooks, "on_message_started") as started:
        run = _runner(injector._start_hook("    "), "self, source, event, session_key")
        asyncio.run(run(runner, source, event, "sk"))
    assert started.call_args.kwargs["message_id"] == "raw"
    assert started.call_args.kwargs["anchor_id"] == "anchor"


def test_followup_boundary_prefers_current_context_over_stale_local() -> None:
    source = SimpleNamespace(platform=SimpleNamespace(value="feishu"), chat_id="chat")
    ctx = SimpleNamespace(event_message_id="redirect-anchor")
    body = injector._followup_complete_hook("    ")
    with (
        patch.object(hooks, "on_queued_followup_boundary", new=AsyncMock(return_value=False)) as boundary,
        patch.object(hooks, "on_queued_followup_card_ready"),
    ):
        run = _runner(
            body, "self, source, turn_ctx, event_message_id, response, result, pending_event, session_key",
        )
        asyncio.run(run(MagicMock(), source, ctx, "stale-local", {}, {}, None, "sk"))
    assert boundary.call_args.kwargs["message_id"] == "redirect-anchor"


def test_followup_done_preserves_deepest_completion_owner() -> None:
    source = SimpleNamespace(platform=SimpleNamespace(value="feishu"), chat_id="chat")
    result = {"final_response": "deep answer", "_hermes_lark_completion_id": "deep-anchor"}
    with patch.object(hooks, "on_queued_followup_completed", new=AsyncMock(return_value=True)) as completed:
        run = _runner(
            injector._followup_done_hook("    "),
            "self, source, turn_ctx, next_message_id, pending_event, followup_result",
        )
        asyncio.run(run(MagicMock(), source, SimpleNamespace(session_key="sk"), "parent-anchor", None, result))
    assert completed.call_args.kwargs["message_id"] == "deep-anchor"
    assert result["_hermes_lark_completion_id"] == "deep-anchor"
    assert result["already_sent"] is True
    assert result["final_response"] == ""


def test_completion_id_uses_current_ctx_and_preserves_deepest() -> None:
    body = injector._completion_id_hook("    ")
    run = _runner(body, "response, turn_ctx", "    return response\n")
    ctx = SimpleNamespace(event_message_id="redirect-anchor", inbound_message_id="raw")
    assert asyncio.run(run({}, ctx))["_hermes_lark_completion_id"] == "redirect-anchor"
    deep = {"_hermes_lark_completion_id": "deep-anchor"}
    assert asyncio.run(run(deep, ctx))["_hermes_lark_completion_id"] == "deep-anchor"


@pytest.fixture()
def current_core(tmp_path: Path) -> dict[str, Path]:
    gateway = Path("/opt/hermes/gateway")
    if not gateway.exists():
        pytest.skip("current Hermes gateway source is unavailable")
    names = {
        "inbound": "run_inbound", "turn": "run_turn", "runner": "run_turn_runner",
        "busy": "run_busy", "notifications": "run_notifications",
    }
    paths = {}
    for name, module in names.items():
        paths[name] = tmp_path / f"{module}.py"
        shutil.copy2(gateway / f"{module}.py", paths[name])
    injector.Patcher(modular_paths=paths).remove()
    assert "# HERMES_LARK_" not in "\n".join(p.read_text() for p in paths.values())
    return paths


def test_current_core_roundtrip_and_marker_distribution(current_core: dict[str, Path]) -> None:
    originals = {name: p.read_text() for name, p in current_core.items()}
    patcher = injector.Patcher(modular_paths=current_core)
    patcher.verify_target()
    patcher.apply()
    assert patcher.is_fully_patched()
    installed = {name: p.read_text() for name, p in current_core.items()}
    combined = "\n".join(installed.values())
    for begin, end in patcher.MARKERS:
        assert combined.count(begin) == combined.count(end) == 1
    for source in installed.values():
        ast.parse(source)
    for marker in ("BUSY_ACK", "BUSY_ACK_GATE", "TURN_REDIRECTED"):
        assert f"# HERMES_LARK_{marker}_BEGIN" in installed["busy"]
    assert "# HERMES_LARK_WATCHER_NOTICE_BEGIN" in installed["notifications"]
    assert "# HERMES_LARK_COMPLETION_ID_BEGIN" in installed["turn"]
    patcher.apply()
    assert installed == {name: p.read_text() for name, p in current_core.items()}
    patcher.remove()
    patcher.remove()
    assert originals == {name: p.read_text() for name, p in current_core.items()}


def test_current_layout_autodetects_optional_notifications(
    current_core: dict[str, Path], monkeypatch, tmp_path: Path,
) -> None:
    gateway = tmp_path / "hermes" / "gateway"
    gateway.mkdir(parents=True)
    for source in current_core.values():
        shutil.copy2(source, gateway / source.name)
    facade = gateway / "run.py"
    facade.write_text("# gateway compatibility facade\n")
    monkeypatch.setattr(injector, "_code_roots", lambda: [gateway.parent])
    monkeypatch.setattr(injector, "_default_run_path", lambda: facade)
    patcher = injector.Patcher()
    assert patcher._has_notifications is True
    assert patcher._modular_paths["notifications"] == gateway / "run_notifications.py"
    patcher.apply()
    assert patcher.is_fully_patched()
    assert patcher.marker_status()[injector.MK_WATCHER_NOTICE]


def test_current_layout_optional_notifications_filtered(current_core: dict[str, Path]) -> None:
    paths = {name: p for name, p in current_core.items() if name != "notifications"}
    patcher = injector.Patcher(modular_paths=paths)
    assert not any("WATCHER_NOTICE" in begin for begin, _ in patcher.MARKERS)
    patcher.apply()
    assert patcher.is_fully_patched()
    assert all(patcher.marker_status().values())
