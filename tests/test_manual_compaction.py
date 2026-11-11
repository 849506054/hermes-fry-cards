"""Manual /compress status lines reach the chat adapter, so the card path can fold them."""
import asyncio

import pytest

from hermes_fry_cards import manual_compaction
from hermes_fry_cards.manual_compaction import apply_patch

START = "🗜️ Compacting context — summarizing earlier conversation so I can continue..."
HEARTBEAT = "🗜️ Compacting context — still summarizing earlier conversation so I can continue..."
DONE = "✓ Context compaction complete — continuing turn..."


class FakeAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, content, metadata=None, **kwargs):
        self.sent.append((chat_id, content))


class FakeSource:
    chat_id = "oc_chat"


class FakeAgent:
    def __init__(self):
        self.status_callback = None


def _mixin(adapter):
    class Mixin:
        def _delivery_adapter_for(self, source):
            return adapter

        async def _run_manual_compression(self, source, session_entry, history, request):
            agent = await self._build_manual_compression_agent("sid", "model", {})
            await asyncio.sleep(0)
            return agent

        async def _build_manual_compression_agent(self, session_id, model, runtime_kwargs):
            return FakeAgent()

    return Mixin


@pytest.mark.asyncio
async def test_manual_compression_agent_emits_status_through_the_adapter():
    adapter = FakeAdapter()
    mixin = _mixin(adapter)
    assert apply_patch(mixin) is True
    agent = await mixin()._run_manual_compression(FakeSource(), None, [], None)
    assert agent.status_callback is not None
    for line in (START, HEARTBEAT, DONE):
        agent.status_callback("lifecycle", line)
    await asyncio.sleep(0.05)
    assert [content for _chat, content in adapter.sent] == [START, HEARTBEAT, DONE]
    assert {chat for chat, _ in adapter.sent} == {"oc_chat"}


@pytest.mark.asyncio
async def test_build_outside_the_manual_run_leaves_the_callback_unset():
    adapter = FakeAdapter()
    mixin = _mixin(adapter)
    apply_patch(mixin)
    agent = await mixin()._build_manual_compression_agent("sid", "model", {})
    assert agent.status_callback is None


@pytest.mark.asyncio
async def test_missing_adapter_skips_the_callback():
    class Mixin:
        def _delivery_adapter_for(self, source):
            raise RuntimeError("no adapter")

        async def _run_manual_compression(self, source, session_entry, history, request):
            return await self._build_manual_compression_agent("sid", "model", {})

        async def _build_manual_compression_agent(self, session_id, model, runtime_kwargs):
            return FakeAgent()

    apply_patch(Mixin)
    agent = await Mixin()._run_manual_compression(FakeSource(), None, [], None)
    assert agent.status_callback is None


@pytest.mark.asyncio
async def test_apply_patch_is_idempotent_and_reports_missing_hooks():
    class Bare:
        pass

    mixin = _mixin(FakeAdapter())
    assert apply_patch(mixin) is True
    assert apply_patch(mixin) is True
    assert mixin._hl_manual_compress_patched is True
    assert apply_patch(Bare) is False
    assert apply_patch(None) is False
    assert manual_compaction._manual_target.get() is None
