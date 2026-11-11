"""Manual /compress progress lines travel the normal send path into the compaction card.

The manual path builds a throwaway quiet AIAgent whose ``status_callback`` is
unset, so the start/heartbeat/complete lines the compression core emits never
reach a chat surface and only the command's final reply lands. This attaches
that callback to the chat adapter: the lines then take ``adapter.send`` like any
other status, where :mod:`system_messages` folds them into one card.
"""
from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from functools import wraps
from typing import Any

_logger = logging.getLogger('hermes_fry_cards')

_manual_target: ContextVar = ContextVar('hl_manual_compress_target', default=None)


def _emitter(adapter: Any, chat_id: str, loop: asyncio.AbstractEventLoop):
    def emit(event_type: str, message: str) -> None:
        text = str(message or '').strip()
        if not text:
            return

        async def _deliver() -> None:
            try:
                await adapter.send(chat_id, text)
            except Exception as exc:
                _logger.warning('manual_compaction_status_failed: type=%s', type(exc).__name__)

        try:
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_deliver()))
        except RuntimeError:
            _logger.debug('manual_compaction_status_dropped: loop closed')
    return emit


def apply_patch(mixin_cls: Any) -> bool:
    """Wrap the manual-compression hooks on the gateway session-commands mixin."""
    if mixin_cls is None:
        return False
    if getattr(mixin_cls, '_hl_manual_compress_patched', False):
        return True
    build = getattr(mixin_cls, '_build_manual_compression_agent', None)
    run = getattr(mixin_cls, '_run_manual_compression', None)
    if build is None or run is None:
        return False

    @wraps(run)
    async def _run(self, source, session_entry, history, request):
        token = None
        try:
            adapter = self._delivery_adapter_for(source)
        except Exception:
            adapter = None
        chat_id = str(getattr(source, 'chat_id', '') or '')
        if adapter is not None and chat_id:
            token = _manual_target.set((adapter, chat_id))
        try:
            return await run(self, source, session_entry, history, request)
        finally:
            if token is not None:
                _manual_target.reset(token)

    @wraps(build)
    async def _build(self, session_id, model, runtime_kwargs):
        agent = await build(self, session_id, model, runtime_kwargs)
        target = _manual_target.get()
        if agent is not None and target is not None:
            adapter, chat_id = target
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return agent
            agent.status_callback = _emitter(adapter, chat_id, loop)
        return agent

    mixin_cls._run_manual_compression = _run
    mixin_cls._build_manual_compression_agent = _build
    mixin_cls._hl_manual_compress_patched = True
    return True
