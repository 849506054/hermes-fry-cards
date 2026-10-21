"""Hook keyword contract and redirect signal routing."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from hermes_fry_cards import patch as hooks


@pytest.mark.parametrize(
    "name", [name for name, value in vars(hooks).items() if name.startswith("on_") and hasattr(value, "__wrapped__")]
)
def test_safe_hook_keywords(name: str) -> None:
    parameters = inspect.signature(getattr(hooks, name).__wrapped__).parameters
    assert {"ctrl", "message_id"} <= parameters.keys()


def test_busy_ack_routes_explicit_message_and_session() -> None:
    ctrl = MagicMock(enabled=True)
    ctrl.on_busy_ack.return_value = True
    with patch.object(hooks, "get_controller", return_value=ctrl):
        assert hooks.on_busy_ack(message_id="anchor-new", session_key="sk", text="busy") is True
    ctrl.on_busy_ack.assert_called_once_with(message_id="anchor-new", session_key="sk", text="busy")


def test_redirect_routes_anchor_and_injected_controller() -> None:
    ctrl = MagicMock(enabled=True)
    with patch.object(hooks, "get_controller", return_value=ctrl):
        hooks.on_turn_redirected(session_key="sk", message_id="anchor-new")
    ctrl.on_turn_redirected.assert_called_once_with(session_key="sk", message_id="anchor-new")


@pytest.mark.parametrize("name", ["on_busy_ack", "on_turn_redirected"])
def test_busy_hooks_disabled_and_failure_defaults(name: str) -> None:
    ctrl = MagicMock(enabled=False)
    call = getattr(hooks, name)
    kwargs = {"message_id": "anchor", "session_key": "sk"}
    if name == "on_busy_ack":
        kwargs["text"] = "busy"
    with patch.object(hooks, "get_controller", return_value=ctrl):
        assert call(**kwargs) in (None, False)
    getattr(ctrl, name).assert_not_called()
    ctrl.enabled = True
    getattr(ctrl, name).side_effect = RuntimeError("delivery failure")
    with patch.object(hooks, "get_controller", return_value=ctrl):
        assert call(**kwargs) in (None, False)
