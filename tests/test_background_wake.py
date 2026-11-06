"""Card receipts and parent wake admission are independent deliveries."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from hermes_fry_cards.patcher import _bg_completion_hook


async def inject(ctrl, *, platform="feishu", event=None, admission_error=None):
    """Run the installed hook shape and the gateway's following admission step."""
    evt = event or {
        "type": "async_delegation", "delegation_id": "deleg_fixture",
        "parent_session_id": "parent_fixture", "session_key": "turn_fixture",
        "results": [{"summary": "Full child result"}],
    }
    text = "[ASYNC DELEGATION BATCH COMPLETE — deleg_fixture]\nFull child result"
    admit = AsyncMock(return_value=True, side_effect=admission_error)
    scope = {
        "platform_name": platform, "source": SimpleNamespace(chat_id="chat_fixture"),
        "evt": evt, "synth_text": text, "admit": admit,
    }
    source = "async def run():\n" + _bg_completion_hook("    ")
    source += "\n    return await admit(synth_text, evt)\n"
    exec(compile(source, "<installed-background-hook>", "exec"), scope)
    with patch("hermes_fry_cards.patch.get_controller", return_value=ctrl):
        result = await scope["run"]()
    return result, admit, text, evt


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [True, False, RuntimeError("receipt unavailable")])
@pytest.mark.parametrize("event_type", ["async_delegation", "completion", "watch_match"])
async def test_receipt_outcome_always_preserves_parent_wake(receipt, event_type):
    ctrl = SimpleNamespace(enabled=True, on_background_completion=AsyncMock())
    if isinstance(receipt, Exception):
        ctrl.on_background_completion.side_effect = receipt
    else:
        ctrl.on_background_completion.return_value = receipt
    evt = {"type": event_type, "session_key": "turn_fixture", "parent_session_id": "parent_fixture"}
    result, admit, text, event = await inject(ctrl, event=evt)
    assert result is True
    admit.assert_awaited_once_with(text, event)
    ctrl.on_background_completion.assert_awaited_once()
    assert ctrl.on_background_completion.call_args.kwargs["text"] == text
    assert event == evt


@pytest.mark.asyncio
async def test_receipt_success_cannot_ack_failed_parent_admission():
    ctrl = SimpleNamespace(enabled=True, on_background_completion=AsyncMock(return_value=True))
    with pytest.raises(RuntimeError, match="admission unavailable"):
        await inject(ctrl, admission_error=RuntimeError("admission unavailable"))
    ctrl.on_background_completion.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_cards_leave_normal_wake_intact():
    ctrl = SimpleNamespace(enabled=False, on_background_completion=AsyncMock(return_value=False))
    result, admit, text, event = await inject(ctrl)
    assert result is True
    admit.assert_awaited_once_with(text, event)
    ctrl.on_background_completion.assert_awaited_once()


@pytest.mark.asyncio
async def test_other_channel_uses_native_wake():
    ctrl = SimpleNamespace(enabled=True, on_background_completion=AsyncMock())
    result, admit, text, event = await inject(ctrl, platform="telegram")
    assert result is True
    admit.assert_awaited_once_with(text, event)
    ctrl.on_background_completion.assert_not_called()
