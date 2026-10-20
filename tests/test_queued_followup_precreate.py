"""#37 — the queued follow-up's card exists before it starts streaming.

Without a card at the start of the follow-up turn, Hermes streams the answer as plain
text and the completion hook adds a synthetic card afterwards: the same answer is
delivered twice. ``on_queued_followup_card_ready`` creates (or reuses) the card at the
boundary, so the follow-up streams into it and the completion hook seals that card.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import hermes_fry_cards.patch as patch_module
from hermes_fry_cards.patch import on_queued_followup_card_ready


def _ctrl(existing: str | None = None, created: str | None = "syn-new") -> MagicMock:
    ctrl = MagicMock()
    ctrl.enabled = True
    ctrl.resolve_session_id.return_value = existing
    ctrl.on_message_started.return_value = created
    return ctrl


def test_reuses_the_card_already_registered_for_this_turn() -> None:
    ctrl = _ctrl(existing="om-existing")
    with patch.object(patch_module, "get_controller", return_value=ctrl):
        assert on_queued_followup_card_ready(
            message_id=None, chat_id="chat", session_key="key"
        ) == "om-existing"

    ctrl.on_message_started.assert_not_called()


def test_creates_a_card_for_a_follow_up_without_an_event_id() -> None:
    ctrl = _ctrl(existing=None, created="syn-abc123")
    with patch.object(patch_module, "get_controller", return_value=ctrl):
        assert on_queued_followup_card_ready(
            message_id=None, chat_id="chat", chat_type="dm", session_key="key"
        ) == "syn-abc123"

    ctrl.on_message_started.assert_called_once_with(
        message_id=None,
        chat_id="chat",
        chat_type="dm",
        anchor_id=None,
        session_key="key",
    )


def test_falls_back_when_no_card_can_be_created() -> None:
    ctrl = _ctrl(existing=None, created=None)
    with patch.object(patch_module, "get_controller", return_value=ctrl):
        assert on_queued_followup_card_ready(
            message_id="om-x", chat_id="chat", session_key="key"
        ) is None
