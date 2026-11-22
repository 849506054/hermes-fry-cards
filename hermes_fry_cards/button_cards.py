"""Button cards on the fry surface: exec approvals, clarify choices, update prompts.

Core builds every button card as JSON 1.0 and hands it to two choke points:
``FeishuAdapter._send_interactive_card`` for the first delivery and
``FeishuAdapter._card_response`` for the click-time replacement. Converting there
covers every button card the adapter sends, including ones added upstream later,
without touching each caller.

Button values (``hermes_action`` / ``approval_id`` / choice ids) are copied into
``behaviors[].value`` unchanged: JSON 2.0 hands that value back at
``event.action.value``, which is where the core click handlers already read it.

The v1 card's colored title bar becomes the first body line; the card keeps the
same notice header the other fry system cards use.
"""
from __future__ import annotations

import json
import logging
import unicodedata
import weakref
from functools import wraps
from typing import Any

from .cardkit.builder import _body_style, _build_body, build_notice_header
from .cardkit.styles import header_element, style_scope, text_element
from .config import Config

_logger = logging.getLogger('hermes_fry_cards')
_CARD_MAX_BYTES = 28000
# 已包过的包装函数：适配器模块被加载两次时，补丁会跑两遍，靠它只包一层
_WRAPPED: weakref.WeakSet[Any] = weakref.WeakSet()

_V2_BUTTON_TYPES = frozenset({
    'default', 'primary', 'danger', 'text', 'primary_text', 'danger_text',
    'primary_filled', 'danger_filled', 'laser',
})


def _button_label(button: dict) -> str:
    text = button.get('text')
    if isinstance(text, dict):
        return str(text.get('content') or '')
    return str(text or '')


def _button_value(button: dict) -> dict:
    value = button.get('value')
    return value if isinstance(value, dict) else {}


def _v2_button(button: dict, index: int) -> dict:
    kind = str(button.get('type') or 'default')
    value = _button_value(button)
    return {
        'tag': 'button',
        'element_id': f'fry_btn_{index}',
        'text': {'tag': 'plain_text', 'content': _button_label(button)},
        'type': kind if kind in _V2_BUTTON_TYPES else 'default',
        # 2.0 走 behaviors 回传；顶层 value 同步写一份，老客户端也认。
        'value': value,
        'behaviors': [{'type': 'callback', 'value': value}],
    }


def _button_row(buttons: list[dict]) -> dict:
    """One flow row: buttons sit side by side and wrap on a narrow screen."""
    return {
        'tag': 'column_set',
        'flex_mode': 'flow',
        'horizontal_spacing': 'default',
        'columns': [
            {'tag': 'column', 'width': 'auto', 'weight': 1, 'vertical_align': 'center',
             'elements': [_v2_button(button, index)]}
            for index, button in enumerate(buttons)
        ],
    }


def _strip_leading_symbols(text: str) -> str:
    """去掉标题开头的表情符号：图标改由标题栏 icon 承担，文案只留文字."""
    index = 0
    for char in text:
        if char.isspace() or unicodedata.category(char) in {'So', 'Mn', 'Sk', 'Cf'}:
            index += 1
            continue
        break
    return text[index:] or text


_CHOICE_MARKS = ('❓', '?')


def _header(title: str, cfg: Config) -> dict:
    """原标题文案 + fry 的标题样式；按句首符号挑图标，并把表情剥掉."""
    icons = cfg.header_icons
    key = icons.get('choice') if title.lstrip().startswith(_CHOICE_MARKS) else icons.get('approval')
    header: dict = {'title': {'tag': 'plain_text', 'content': title}}
    if cfg.header_template != 'default':
        header['template'] = cfg.header_template
    if key:
        header['title']['content'] = _strip_leading_symbols(title)
        header['icon'] = {'tag': 'custom_icon', 'img_key': key}
    return header_element(header)


def to_v2(card: Any, config: Config | None = None) -> Any:
    """Return *card* as a 2.0 card; anything already 2.0 (or not a card) passes through."""
    if not isinstance(card, dict) or card.get('schema') == '2.0':
        return card
    cfg = config or Config().card_snapshot()
    raw_header = card.get('header')
    header: dict = raw_header if isinstance(raw_header, dict) else {}
    title = ''
    raw_title = header.get('title')
    if isinstance(raw_title, dict):
        title = str(raw_title.get('content') or '').strip()
    texts: list[str] = []
    buttons: list[dict] = []
    for element in card.get('elements') or []:
        if not isinstance(element, dict):
            continue
        tag = element.get('tag')
        if tag == 'markdown':
            content = str(element.get('content') or '')
            if content.strip():
                texts.append(content)
        elif tag == 'action':
            buttons.extend(b for b in (element.get('actions') or []) if isinstance(b, dict))
    if not texts and not buttons and not title:
        return card
    summary = (texts[0] if texts else title).replace('\n', ' ').strip()
    with style_scope(cfg):
        elements: list[dict] = []
        for content in texts:
            elements.append(text_element(
                {'tag': 'markdown', 'content': content, 'text_size': cfg.body_text_size}, 'body'))
        if buttons:
            elements.append(_button_row(buttons))
        converted = {
            'schema': '2.0',
            'config': {
                'width_mode': cfg.width_mode,
                'style': _body_style(cfg),
                'summary': {'content': summary[:120]},
            },
            'body': _build_body(elements),
        }
        raw_config = card.get('config')
        if isinstance(raw_config, dict) and raw_config.get('update_multi'):
            # 共享卡（clarify / picker）：PATCH 要能落到群里每个人的那份，2.0 同样认这个开关
            converted['config']['update_multi'] = True
        if title:  # 原标题（如「⚠️ Hermes 想要运行一条需要你确认的命令」）仍是标题栏，只换成 fry 的标题样式
            converted['header'] = _header(title, cfg)
        elif cfg.header_enabled:
            converted['header'] = build_notice_header(config=cfg)
    try:
        size = len(json.dumps(converted, ensure_ascii=False).encode())
    except (TypeError, ValueError):
        return card
    if size > _CARD_MAX_BYTES:  # 换算后的卡超出整卡体积闸门就留原卡，宁可样式不统一也不发不出去
        _logger.warning('button_card_skipped: reason=over_budget bytes=%d', size)
        return card
    _logger.debug('button_card_v2: title=%s buttons=%d bytes=%d', title[:40], len(buttons), size)
    return converted


def _wrap_send(original: Any) -> Any:
    """把转换包在捕获到的原函数上：模块被加载两次、补丁挂到两个类上时也不会自递归."""
    @wraps(original)
    async def wrapper(self, chat_id, card, metadata, failure_message, **kwargs):
        try:
            if Config().enabled:
                card = to_v2(card)
        except Exception as exc:  # 转换出错就按原卡发，审批不能因为样式挂掉
            _logger.warning('button_card_convert_failed: stage=send type=%s', type(exc).__name__)
        return await original(self, chat_id, card, metadata, failure_message, **kwargs)
    _WRAPPED.add(wrapper)
    return wrapper


def _wrap_card_response(original: Any) -> Any:
    def wrapper(card_data=None):
        try:
            if card_data is not None and Config().enabled:
                card_data = to_v2(card_data)
        except Exception as exc:
            _logger.warning('button_card_convert_failed: stage=callback type=%s', type(exc).__name__)
        return original(card_data)
    _WRAPPED.add(wrapper)
    return wrapper


def _wrap_settled_clarify(original: Any) -> Any:
    """Clarify 收尾卡（点击后与超时退役都走它）：同样换成 2.0 卡面."""
    def wrapper(cls_, state, line, template):
        card = original(cls_, state, line, template)
        try:
            if card is not None and Config().enabled:
                card = to_v2(card)
        except Exception as exc:
            _logger.warning('button_card_convert_failed: stage=clarify_settle type=%s', type(exc).__name__)
        return card
    _WRAPPED.add(wrapper)
    return wrapper


def _unwrap(member: Any) -> Any:
    return member.__func__ if isinstance(member, (staticmethod, classmethod)) else member


def apply_patch(cls: Any) -> bool:
    """Wrap the card choke points on the Feishu adapter; each wrapper is applied once per class."""
    if not all(hasattr(cls, name) for name in ('_send_interactive_card', '_card_response')):
        return False
    applied = 0
    current = _unwrap(cls.__dict__.get('_send_interactive_card')) or cls._send_interactive_card
    if current not in _WRAPPED:
        cls._send_interactive_card = _wrap_send(cls._send_interactive_card)
        applied += 1
    current = _unwrap(cls.__dict__.get('_card_response')) or cls._card_response
    if current not in _WRAPPED:
        cls._card_response = staticmethod(_wrap_card_response(cls._card_response))
        applied += 1
    settled = getattr(cls, '_settled_clarify_card', None)
    settled_fn = getattr(settled, '__func__', None)
    if settled_fn is not None and settled_fn not in _WRAPPED:
        # clarify 收尾卡不经过上面两个入口就自己 PATCH，单独挂钩；退役与点击两条路都在这里汇合
        cls._settled_clarify_card = classmethod(_wrap_settled_clarify(settled_fn))
        applied += 1
    if applied:
        _logger.info('button_card_patch_applied: wrappers=%d', applied)
    return True
