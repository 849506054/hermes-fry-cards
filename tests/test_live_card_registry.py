"""#43 网关重启封卡：跨越进程的活跃卡登记与启动清扫.

Hermes 网关重启/崩溃时，正在流式的卡片不会被收尾，用户会一直看到一张转圈的卡。
这里钉住：卡片建好后登记到 profile 的 ``state/fry-cards-live.json``，正常收尾即注销，
进程启动时把「登记过但不属于本进程」的卡统一封成「已中断」。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_controller import _mock_client, _setup_ctrl

from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.streaming.session import CardSession, SessionState


def _session(message_id: str = "msg_live", card_msg_id: str = "om_card") -> CardSession:
    session = CardSession(message_id, "oc_chat", __import__("asyncio").new_event_loop())
    session.state = SessionState.STREAMING
    session.card_id = "card_1"
    session.card_msg_id = card_msg_id
    return session


def _registry(tmp_path: Path, ctrl: StreamCardController) -> Path:
    return tmp_path / "state" / "fry-cards-live.json"


@pytest.fixture()
def ctrl(tmp_path: Path) -> StreamCardController:
    controller = StreamCardController(profile_home=tmp_path)
    controller._initialized = True
    controller._client = _mock_client()
    return controller


def test_track_live_card_writes_registry(tmp_path: Path, ctrl: StreamCardController) -> None:
    session = _session()
    ctrl._track_live_card(session)

    data = json.loads(_registry(tmp_path, ctrl).read_text(encoding="utf-8"))
    assert data["om_card"]["card_id"] == "card_1"
    assert data["om_card"]["chat_id"] == "oc_chat"


def test_untrack_removes_and_cleans_empty_file(tmp_path: Path, ctrl: StreamCardController) -> None:
    session = _session()
    ctrl._track_live_card(session)
    ctrl._untrack_live_card(session)
    assert not _registry(tmp_path, ctrl).exists()


def test_track_is_idempotent_and_ignores_cardless_session(ctrl: StreamCardController) -> None:
    session = _session()
    ctrl._track_live_card(session)
    ctrl._track_live_card(session)
    assert len(ctrl._read_live_cards()) == 1

    cardless = _session(card_msg_id="")
    ctrl._track_live_card(cardless)
    assert len(ctrl._read_live_cards()) == 1


def test_unreadable_registry_is_treated_as_empty(tmp_path: Path, ctrl: StreamCardController) -> None:
    path = _registry(tmp_path, ctrl)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert ctrl._read_live_cards() == {}


@pytest.mark.asyncio
async def test_sweep_closes_leftover_cards(tmp_path: Path, ctrl: StreamCardController) -> None:
    """上一进程遗留的卡：封成「已中断」，并从登记表移除."""
    session = _session()
    ctrl._track_live_card(session)
    ctrl._sessions.clear()  # 模拟新进程：登记还在，但本进程没有会话

    await ctrl._sweep_stale_cards()

    ctrl._client.cardkit_update.assert_awaited_once()
    card_id, card = ctrl._client.cardkit_update.await_args.args[0], ctrl._client.cardkit_update.await_args.args[1]
    assert card_id == "card_1"
    assert "重启" in card["body"]["elements"][0]["i18n_content"]["zh_cn"]
    assert ctrl._read_live_cards() == {}


@pytest.mark.asyncio
async def test_sweep_skips_cards_owned_by_current_process(ctrl: StreamCardController) -> None:
    session = _session()
    ctrl._sessions[session.message_id] = session
    ctrl._track_live_card(session)

    await ctrl._sweep_stale_cards()

    ctrl._client.cardkit_update.assert_not_awaited()
    assert "om_card" in ctrl._read_live_cards()


@pytest.mark.asyncio
async def test_sweep_retries_then_drops_after_max_attempts(ctrl: StreamCardController) -> None:
    """封不掉时累计次数，达到上限后出列，避免反复请求飞书."""
    from hermes_fry_cards.controller import _LIVE_CARD_MAX_CLOSE_ATTEMPTS

    session = _session()
    ctrl._track_live_card(session)
    ctrl._client.cardkit_update = AsyncMock(side_effect=RuntimeError("closed card"))

    for _ in range(_LIVE_CARD_MAX_CLOSE_ATTEMPTS):
        await ctrl._sweep_stale_cards()

    assert "om_card" not in ctrl._read_live_cards()
    assert ctrl._client.cardkit_update.await_count == _LIVE_CARD_MAX_CLOSE_ATTEMPTS


@pytest.mark.asyncio
async def test_sweep_retry_keeps_entry_before_limit(ctrl: StreamCardController) -> None:
    session = _session()
    ctrl._track_live_card(session)
    ctrl._client.cardkit_update = AsyncMock(side_effect=RuntimeError("temporary"))

    await ctrl._sweep_stale_cards()

    record = ctrl._read_live_cards()["om_card"]
    assert record["attempts"] == 1
    assert record["card_id"] == "card_1"


@pytest.mark.asyncio
async def test_sweep_drops_entries_without_card_id(ctrl: StreamCardController) -> None:
    ctrl._write_live_cards({"om_broken": {"chat_id": "oc_chat"}})
    await ctrl._sweep_stale_cards()
    assert ctrl._read_live_cards() == {}
    ctrl._client.cardkit_update.assert_not_awaited()


def test_sweep_runs_once_per_process(ctrl: StreamCardController, monkeypatch) -> None:
    scheduled: list[object] = []

    class _Loop:
        is_closed = staticmethod(lambda: False)

    monkeypatch.setattr(ctrl, "_get_loop", lambda: _Loop())
    monkeypatch.setattr(ctrl, "_fire_and_forget", lambda coro, loop: scheduled.append(coro))

    ctrl._maybe_sweep_stale_cards()
    ctrl._maybe_sweep_stale_cards()

    assert ctrl._swept_stale_cards is True
    assert ctrl._stale_sweep_attempts == 1
    assert len(scheduled) == 1
    scheduled[0].close()  # type: ignore[attr-defined]


def test_sweep_retries_when_no_loop(ctrl: StreamCardController, monkeypatch) -> None:
    from hermes_fry_cards.controller import _STALE_SWEEP_MAX_ATTEMPTS

    monkeypatch.setattr(ctrl, "_get_loop", lambda: None)
    for _ in range(_STALE_SWEEP_MAX_ATTEMPTS + 2):
        ctrl._maybe_sweep_stale_cards()

    assert ctrl._swept_stale_cards is False
    assert ctrl._stale_sweep_attempts == _STALE_SWEEP_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_dispose_untracks_card(tmp_path: Path, ctrl: StreamCardController) -> None:
    """正常收尾的卡不应在下次启动时被误封."""
    session = _session()
    ctrl._sessions[session.message_id] = session
    ctrl._track_live_card(session)

    ctrl._dispose_session(session)

    assert ctrl._read_live_cards() == {}


def test_setup_ctrl_has_sweep_state() -> None:
    ctrl = _setup_ctrl()
    assert ctrl._swept_stale_cards is False
    assert ctrl._stale_sweep_attempts == 0
