"""Busy ack hook: the gateway's notice goes into the live card, not a second message."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import hermes_fry_cards.patch as patch_module
from hermes_fry_cards.patch import on_busy_ack


def test_hook_forwards_the_ack_to_the_controller() -> None:
    ctrl = MagicMock()
    ctrl.enabled = True
    ctrl.on_busy_ack.return_value = True
    with patch.object(patch_module, "get_controller", return_value=ctrl):
        assert on_busy_ack(
            message_id="om_x", session_key="key", text="⏳ 已排队至下一回合"
        ) is True

    ctrl.on_busy_ack.assert_called_once_with(
        session_key="key", text="⏳ 已排队至下一回合"
    )


def test_hook_is_inert_when_the_plugin_is_disabled() -> None:
    ctrl = MagicMock()
    ctrl.enabled = False
    with patch.object(patch_module, "get_controller", return_value=ctrl):
        assert on_busy_ack(message_id="om_x", session_key="key", text="x") is False

    ctrl.on_busy_ack.assert_not_called()
