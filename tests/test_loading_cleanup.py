from unittest.mock import AsyncMock

import pytest

from hermes_fry_cards.cardkit.builder import LOADING_CURSOR_ELEMENT_ID, LOADING_ELEMENT_ID
from hermes_fry_cards.streaming.controller import StreamingController


@pytest.mark.asyncio
async def test_loading_cleanup_deletes_anchor_and_cursor():
    controller = object.__new__(StreamingController)
    controller._client = AsyncMock()
    await controller._remove_loading_icon('card', 7)
    controller._client.cardkit_batch_update.assert_awaited_once_with(
        'card', [{'action': 'delete_elements', 'params': {
            'element_ids': [LOADING_ELEMENT_ID, LOADING_CURSOR_ELEMENT_ID],
        }}], sequence=7,
    )
