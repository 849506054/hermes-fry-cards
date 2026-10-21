"""Injector compatibility against the real Hermes v0.22.0 sources (#34 / #35).

The hand-written fixtures in ``test_patcher_modular.py`` are minimal and can drift from
upstream without anyone noticing. These tests run the real patcher over the actual
``gateway/run_turn.py`` and ``gateway/run_turn_runner.py`` from the ``v0.22.0`` release,
so a Hermes bump that moves an injection anchor fails here rather than at runtime on a
live gateway.

Source resolution follows the same convention as ``test_patcher.py``: prefer the local
install, else fetch a **pinned tag** (never a moving branch), else skip.

Both follow-up hooks anchor on the same ``_preserve_queued_followup_history_offset``
line, so ordering is load-bearing: ``FOLLOWUP_DONE`` must land first — it writes
``_hermes_lark_completion_id`` = the card it actually sealed, and ``FOLLOWUP_RESULT``'s
``setdefault`` must not overwrite that with the raw inbound id.
"""
from __future__ import annotations

import ast
import shutil
import urllib.request
from pathlib import Path

import pytest

from hermes_fry_cards.patcher import (
    MK_FOLLOWUP_COMPLETE,
    MK_FOLLOWUP_COMPLETE_END,
    MK_FOLLOWUP_DONE,
    MK_FOLLOWUP_RESULT,
    Patcher,
)

# Pinned release, not a moving branch: a main-branch fixture would silently drift.
_HERMES_TAG = "v0.22.0"
_BASE_URL = f"https://raw.githubusercontent.com/NousResearch/hermes-agent/{_HERMES_TAG}/gateway"

_HERMES_HOME = Path.home() / ".hermes" / "hermes-agent" / "gateway"
SAMPLES_DIR = Path(__file__).parent / "samples" / f"modular_{_HERMES_TAG}"


def _ensure_sample(name: str) -> Path:
    """Local install first, then a pinned-tag download, else skip."""
    local = _HERMES_HOME / name
    if local.exists() and "_run_agent" in local.read_text(encoding="utf-8"):
        return local

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    dest = SAMPLES_DIR / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    try:
        urllib.request.urlretrieve(f"{_BASE_URL}/{name}", dest)
    except Exception as exc:  # offline / rate-limited / moved file
        pytest.skip(f"{name} not available locally and download failed: {exc}")
    if not dest.exists() or dest.stat().st_size == 0:
        pytest.skip(f"{name} download returned empty file")
    return dest


@pytest.fixture()
def real_v022(tmp_path: Path) -> dict[str, Path]:
    turn_src = _ensure_sample("run_turn.py")
    runner_src = _ensure_sample("run_turn_runner.py")

    # Only these two are stubbed — the patcher needs their entry points present, and
    # neither hosts an anchor under test.
    paths = {
        "inbound": tmp_path / "run_inbound.py",
        "turn": tmp_path / "run_turn.py",
        "runner": tmp_path / "run_turn_runner.py",
        "busy": tmp_path / "run_busy.py",
    }
    paths["inbound"].write_text(
        "async def _handle_message(self, event):\n"
        "    _admitted = await self._admit(event)\n"
        "    source = event.source\n",
        encoding="utf-8",
    )
    paths["busy"].write_text(
        "async def stop(self, session_key):\n"
        "    await self._interrupt_and_clear_session(\n"
        "        session_key, invalidation_reason='stop_command',\n"
        "    )\n",
        encoding="utf-8",
    )
    import textwrap

    from test_patcher_modular import MODULAR_SOURCES

    paths["busy"].write_text(textwrap.dedent(MODULAR_SOURCES["busy"]), encoding="utf-8")
    shutil.copy2(turn_src, paths["turn"])
    shutil.copy2(runner_src, paths["runner"])
    return paths


def test_every_injection_anchor_exists_in_v022(real_v022: dict[str, Path]) -> None:
    """If a Hermes release moves an anchor, verify must fail loudly — never silently skip."""
    Patcher(modular_paths=real_v022).verify_target()


def test_apply_is_valid_python_and_installs_each_marker_once(real_v022: dict[str, Path]) -> None:
    patcher = Patcher(modular_paths=real_v022)
    patcher.apply()

    assert patcher.is_fully_patched()
    for path in real_v022.values():
        ast.parse(path.read_text(encoding="utf-8"))

    combined = "\n".join(p.read_text(encoding="utf-8") for p in real_v022.values())
    for begin, end in patcher.MARKERS:
        assert combined.count(begin) == 1, f"{begin} installed {combined.count(begin)}x"
        assert combined.count(end) == 1, f"{end} installed {combined.count(end)}x"


def test_followup_done_seals_card_before_merge(real_v022: dict[str, Path]) -> None:
    """#35: the follow-up's own card is sealed before the chain merges its result.

    Both follow-up hooks must land before the merge line, and only after
    ``followup_result`` is bound by the recursive ``_run_agent`` call.
    """
    Patcher(modular_paths=real_v022).apply()
    lines = real_v022["turn"].read_text(encoding="utf-8").splitlines()

    done = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_DONE in line)
    result = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_RESULT in line)
    merge = next(
        i for i, line in enumerate(lines)
        if "_preserve_queued_followup_history_offset(result, followup_result)" in line
        and not line.strip().startswith("# HERMES_LARK")
    )
    bound = next(i for i, line in enumerate(lines) if "followup_result = await self._run_agent(" in line)

    assert bound < done < merge, "hook must sit between the recursive call and the merge"
    assert result < merge
    assert done < result, "FOLLOWUP_DONE first, so FOLLOWUP_RESULT's setdefault can't clobber its id"

    body = "\n".join(lines[done:merge])
    assert "on_queued_followup_completed" in body, "must seal the follow-up turn's card"
    assert "already_sent" in body and "final_response" in body, "must suppress the plain-text send"
    # An event-less follow-up carries no inbound id; it must reuse the card the boundary
    # pre-created for this turn (falling back to creating one when there is none). (#37)
    assert "on_queued_followup_card_ready" in body and "message_id=None" in body


def test_followup_card_precreated_before_recursion(real_v022: dict[str, Path]) -> None:
    """#37 duplicate delivery: the follow-up turn's card exists BEFORE it starts running.

    Without a card at stream time Hermes streams that turn's reply as plain text, and the
    completion hook then adds a card carrying the same text — the reply lands twice.
    """
    Patcher(modular_paths=real_v022).apply()
    lines = real_v022["turn"].read_text(encoding="utf-8").splitlines()

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


def test_apply_is_idempotent_and_remove_restores(real_v022: dict[str, Path]) -> None:
    originals = {k: p.read_text(encoding="utf-8") for k, p in real_v022.items()}
    patcher = Patcher(modular_paths=real_v022)

    patcher.apply()
    installed = {k: p.read_text(encoding="utf-8") for k, p in real_v022.items()}
    patcher.apply()
    assert installed == {k: p.read_text(encoding="utf-8") for k, p in real_v022.items()}

    patcher.remove()
    assert originals == {k: p.read_text(encoding="utf-8") for k, p in real_v022.items()}
