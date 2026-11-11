"""Static cards for explicitly scoped Feishu system notices."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import sys
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any

from .cardkit.builder import _body_style, _build_body, build_notice_header, count_elements_recursive
from .cardkit.styles import style_scope, text_element
from .compaction_card import CompactionCards, build_compaction_card
from .compaction_card import classify as classify_compaction
from .config import Config

_logger = logging.getLogger('hermes_fry_cards')
_CARD_MAX_BYTES = 28000
_MAX_TRACKED = 2048
_notice_context: ContextVar[dict | None] = ContextVar('fry_system_notice', default=None)


@contextmanager
def system_notice_scope(*, chat_type: str = '', enabled: bool = True):
    """Explicit semantic producer scope; never infer the kind from its text."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    token = _notice_context.set({'chat_type': chat_type, 'task': task} if enabled else None)
    try:
        yield
    finally:
        _notice_context.reset(token)


def _scope_method(cls, name: str, *, commands_only: bool = False):
    if cls is None:
        return False
    descriptor = cls.__dict__.get(name)
    if descriptor is None:
        return False
    static = isinstance(descriptor, staticmethod)
    original = descriptor.__func__ if static else descriptor
    if getattr(original, '_fry_notice_scoped', False):
        return True
    signature = inspect.signature(original)

    @wraps(original)
    async def scoped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        event = bound.arguments.get('event')
        source = bound.arguments.get('source') or getattr(event, 'source', None)
        enabled = not commands_only or bool(event and event.is_command())
        watcher = bound.arguments.get('watcher') or {}
        chat_type = str(getattr(source, 'chat_type', '') or watcher.get('chat_type') or '')
        with system_notice_scope(chat_type=chat_type, enabled=enabled):
            return await original(*args, **kwargs)

    scoped._fry_notice_scoped = True
    setattr(cls, name, staticmethod(scoped) if static else scoped)
    return True


def apply_producer_scopes(*, import_missing: bool = False):
    """Mark supported existing producer methods without changing their calls or returns."""
    producers = [
        ('gateway.platforms.base', 'BasePlatformAdapter', '_dispatch_inline_reply', True),
        ('gateway.platforms.base', 'BasePlatformAdapter', 'send_final_ledgered', True),
        ('gateway.platforms.base', 'BasePlatformAdapter', '_notify_turn_error', False),
        ('gateway.run_shutdown', 'GatewayShutdownMixin', '_send_notice_logged', False),
        ('gateway.run_notifications', 'GatewayNotificationsMixin', '_send_restart_notification', False),
        ('gateway.run_notifications', 'GatewayNotificationsMixin', '_send_home_channel_message', False),
        ('gateway.run_notifications', 'GatewayNotificationsMixin', '_send_watcher_message', False),
        ('gateway.run_notifications', 'GatewayNotificationsMixin', '_deliver_platform_notice', False),
        ('gateway.run_busy', 'GatewayBusySessionMixin', '_send_busy_reply', False),
        ('gateway.run_busy', 'GatewayBusySessionMixin', '_send_command_ack', False),
        ('gateway.slash_commands_model', 'GatewayModelCommandsMixin', '_offer_usage_limit_switch', False),
    ]
    applied = 0
    for module_name, class_name, name, commands_only in producers:
        # Importing partially initialized gateway classes can form a boot cycle.
        # Retry unavailable classes after the adapter connects.
        try:
            module = sys.modules.get(module_name)
            if module is None and import_missing:
                module = importlib.import_module(module_name)
            cls = getattr(module, class_name, None)
            if cls is not None:
                applied += bool(_scope_method(cls, name, commands_only=commands_only))
        except ImportError:
            _logger.debug('system_card_scope_deferred: module=%s', module_name)
    try:
        module = sys.modules.get('gateway.run_notifications_update')
        if module is None and import_missing:
            module = importlib.import_module('gateway.run_notifications_update')
        cls = getattr(getattr(module, 'GatewayUpdateNotificationsMixin', None), '_UpdateTarget', None)
        applied += bool(_scope_method(cls, 'send'))
    except (ImportError, AttributeError):
        _logger.debug('system_card_scope_deferred: module=update_target')
    # Manual /compress builds a quiet agent whose status callback is unset; attach it so the
    # compaction lines reach this card path too. Same late-import retry as the producer scopes.
    try:
        from .manual_compaction import apply_patch as _apply_manual_compaction
        module = sys.modules.get('gateway.slash_commands_session')
        if module is None and import_missing:
            module = importlib.import_module('gateway.slash_commands_session')
        applied += bool(_apply_manual_compaction(getattr(module, 'GatewaySessionCommandsMixin', None)))
    except ImportError:
        _logger.debug('system_card_scope_deferred: module=slash_commands_session')
    return applied


async def _plain_fallback(self, chat_id: str, content: str, *, reply_to, metadata):
    # Text escape routes remain text even when reached from a marked notice producer.
    with system_notice_scope(enabled=False):
        return await self._hl_system_original_plain_fallback(
            chat_id, content, reply_to=reply_to, metadata=metadata)


def build_system_card(content: str, config: Config, *, has_reference: bool = False) -> dict:
    """Keep original notice text and theme; title standalone messages."""
    with style_scope(config):
        element = text_element({'tag': 'markdown', 'content': content,
                                'text_size': config.body_text_size}, 'body')
        # Notice text is already authored upstream; decoration must not truncate or rewrite it.
        element['content'] = content
        card = {'schema': '2.0', 'config': {'width_mode': config.width_mode,
                'style': _body_style(config),
                'summary': {'content': content[:120].replace('\n', ' ').strip()}},
                'body': _build_body([element])}
        if not has_reference and config.header_enabled:
            card['header'] = build_notice_header(config=config)
        return card


def _payload(content: str, config: Config, *, has_reference: bool = False) -> str:
    card = build_system_card(content, config, has_reference=has_reference)
    encoded = json.dumps(card, ensure_ascii=False)
    if len(encoded.encode()) > _CARD_MAX_BYTES or count_elements_recursive(card) > 190:
        raise ValueError('system card exceeds payload budget')
    return encoded


def _allowed(config: Config, metadata: dict | None) -> bool:
    context = _notice_context.get()
    if (context is None or context.get('task') is not asyncio.current_task()
            or not config.enabled or (metadata or {}).get('expect_edits')):
        return False
    allowed = config.chat_types
    chat_type = str((metadata or {}).get('chat_type') or context.get('chat_type') or '')
    return allowed is None or chat_type in allowed


async def _send(self, chat_id: str, content: str, reply_to=None, metadata=None):
    original = self._hl_system_original_send
    if not self._client:
        return await original(chat_id, content, reply_to=reply_to, metadata=metadata)
    if classify_compaction(content) is not None:
        compaction = await _send_compaction(
            self, chat_id, content, reply_to=reply_to, metadata=metadata)
        if compaction is not None:
            return compaction
    config = Config().card_snapshot()
    formatted = self.format_message(content)
    if not formatted or not _allowed(config, metadata):
        return await original(chat_id, content, reply_to=reply_to, metadata=metadata)
    chunks = self.truncate_message(formatted, self.MAX_MESSAGE_LENGTH)
    # Match the adapter's effective reply route: metadata reply ids apply inside topics.
    has_reference = bool(reply_to or ((metadata or {}).get('thread_id')
                                     and (metadata or {}).get('reply_to_message_id')))
    try:
        payloads = [_payload(chunk, config, has_reference=has_reference) for chunk in chunks]
    except Exception:
        _logger.warning('system_card_fallback: reason=build_failed')
        return await original(chat_id, content, reply_to=reply_to, metadata=metadata)
    last = None
    for index, (chunk, payload) in enumerate(zip(chunks, payloads)):
        try:
            response = await self._feishu_send_with_retry(
                chat_id=chat_id, msg_type='interactive', payload=payload,
                reply_to=reply_to, metadata=metadata,
            )
            last = self._finalize_send_result(response, 'system card send failed')
        except Exception as exc:
            # Preserve timeout classification in the caller's existing retry policy.
            _logger.warning('system_card_send_failed: reason=transport_exception type=%s', type(exc).__name__)
            timed_out = isinstance(exc, TimeoutError)
            error = 'System card transport timed out' if timed_out else str(exc) or type(exc).__name__
            result = self._hl_SendResult(success=False, error=error)
            if index and hasattr(result, 'raw_response'):
                result.raw_response = {'partial_overflow': True}
            return result
        if not last.success:
            _logger.warning('system_card_fallback: reason=api_rejected')
            # Earlier chunks are already delivered; retry only the remaining content.
            result = await original(chat_id, ''.join(chunks[index:]),
                                    reply_to=reply_to, metadata=metadata)
            if index and not result.success and hasattr(result, 'raw_response'):
                result.raw_response = {'partial_overflow': True}
            return result
        message_id = getattr(last, 'message_id', None)
        if message_id:
            registry = getattr(self, '_hl_system_cards', None)
            if registry is None:
                registry = self._hl_system_cards = OrderedDict()
            registry[str(message_id)] = (str(chat_id), config, has_reference)
            while len(registry) > _MAX_TRACKED:
                registry.popitem(last=False)
        _logger.info('system_card_sent: chars=%d chunk=%d/%d header=%s',
                     len(chunk), index + 1, len(chunks), not has_reference)
    return last


async def _send_compaction(self, chat_id: str, content: str, *, reply_to=None, metadata=None):
    """Route a context-compaction status line into the turn's shared card.

    The first line posts a card, heartbeat and completion lines patch that same
    card, so three routine notices land as one message instead of three bubbles.
    """
    cards = getattr(self, '_hl_compaction_cards', None)
    if cards is None:
        cards = self._hl_compaction_cards = CompactionCards()
    entry = cards.take(chat_id, content)
    if entry is None:
        return None
    config = Config().card_snapshot()
    card = build_compaction_card(list(entry['lines']), config)
    payload = json.dumps(card, ensure_ascii=False)
    has_reference = bool(reply_to or ((metadata or {}).get('thread_id')
                                     and (metadata or {}).get('reply_to_message_id')))
    message_id = entry.get('message_id')
    try:
        if message_id:
            from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody
            request = PatchMessageRequest.builder().message_id(message_id).request_body(
                PatchMessageRequestBody.builder().content(payload).build()).build()
            response = await self._run_blocking(self._client.im.v1.message.patch, request)
            result = self._finalize_send_result(response, 'compaction card edit failed')
            if result.success:
                result.message_id = message_id
            _logger.info('compaction_card_updated: lines=%d success=%s',
                         len(entry['lines']), result.success)
            return result
        response = await self._feishu_send_with_retry(
            chat_id=chat_id, msg_type='interactive', payload=payload,
            reply_to=reply_to, metadata=metadata,
        )
        result = self._finalize_send_result(response, 'compaction card send failed')
    except Exception as exc:
        _logger.warning('compaction_card_failed: reason=transport type=%s', type(exc).__name__)
        timed_out = isinstance(exc, TimeoutError)
        return self._hl_SendResult(
            success=False,
            error='Compaction card transport timed out' if timed_out else (str(exc) or type(exc).__name__),
        )
    if result.success:
        new_id = getattr(result, 'message_id', None)
        if new_id:
            cards.bind(chat_id, entry, new_id)
            registry = getattr(self, '_hl_system_cards', None)
            if registry is None:
                registry = self._hl_system_cards = OrderedDict()
            registry[str(new_id)] = (str(chat_id), config, has_reference)
            while len(registry) > _MAX_TRACKED:
                registry.popitem(last=False)
    _logger.info('compaction_card_sent: lines=%d success=%s', len(entry['lines']), result.success)
    return result


async def _edit(self, chat_id: str, message_id: str, content: str, *, finalize=False):
    original = self._hl_system_original_edit
    entry = getattr(self, '_hl_system_cards', {}).get(str(message_id))
    if entry is None or entry[0] != str(chat_id) or not self._client:
        return await original(chat_id, message_id, content, finalize=finalize)
    try:
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody
        payload = _payload(self.format_message(content), entry[1], has_reference=entry[2])
        request = PatchMessageRequest.builder().message_id(message_id).request_body(
            PatchMessageRequestBody.builder().content(payload).build()).build()
        response = await self._run_blocking(self._client.im.v1.message.patch, request)
        result = self._finalize_send_result(response, 'system card edit failed')
        if result.success:
            result.message_id = message_id
        _logger.info('system_card_edited: chars=%d success=%s', len(content), result.success)
        return result
    except Exception:
        _logger.warning('system_card_edit_failed')
        return self._hl_SendResult(success=False, error='System card edit failed')


def apply_patch(cls: Any, *, SendResult: Any) -> bool:
    """Attach to the existing Feishu adapter patch; rich-card/media methods stay intact."""
    if getattr(cls, '_hl_system_patched', False):
        return True
    if not all(hasattr(cls, name) for name in ('send', 'edit_message', '_feishu_send_with_retry')):
        return False
    cls._hl_system_original_send = cls.send
    cls._hl_system_original_edit = cls.edit_message
    cls._hl_SendResult = SendResult
    cls.send = wraps(cls.send)(_send)
    cls.edit_message = wraps(cls.edit_message)(_edit)
    cls._hl_system_patched = True
    if hasattr(cls, '_send_plain_fallback'):
        cls._hl_system_original_plain_fallback = cls._send_plain_fallback
        cls._send_plain_fallback = wraps(cls._send_plain_fallback)(_plain_fallback)
    if hasattr(cls, 'connect'):
        original_connect = cls.connect

        @wraps(original_connect)
        async def connect(self, *args, **kwargs):
            result = await original_connect(self, *args, **kwargs)
            apply_producer_scopes(import_missing=True)
            return result

        cls.connect = connect
    scoped = apply_producer_scopes()
    _logger.info('system_card_patch_applied: marked_notice=card producers=%d ordinary_send=original', scoped)
    return True
