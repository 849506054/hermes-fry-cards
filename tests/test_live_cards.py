"""Live-card registry: restart leaves no card spinning forever."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes_fry_cards.controller import StreamCardController


@pytest.mark.asyncio
async def test_sweep_closes_cards_left_live(tmp_path) -> None:
    ctrl = StreamCardController(profile_home=tmp_path)
    ctrl._client = MagicMock()
    ctrl._client.patch_card_message = AsyncMock()

    path = ctrl._live_cards_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"om_left_over": {"card_id": "c1", "chat_id": "chat"}}),
        encoding="utf-8",
    )

    await ctrl._sweep_stale_cards()

    ctrl._client.patch_card_message.assert_awaited_once()
    assert ctrl._client.patch_card_message.await_args.args[0] == "om_left_over"
    assert not path.exists()
