"""A card retains its sampled style through incremental writes and callbacks."""
from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from hermes_fry_cards.clarify import build_clarify_card, build_resolved_clarify_card
from hermes_fry_cards.config import Config
from hermes_fry_cards.controller import StreamCardController, _interrupted_card
from hermes_fry_cards.streaming.segment_helper import (
    build_add_segment_action,
    build_reasoning_finalized_action,
    build_tool_update_action,
)
from hermes_fry_cards.streaming.segments import SegmentState
from hermes_fry_cards.streaming.session import CardSession


def configure(home, color):
    (home / "config.yaml").write_text(yaml.safe_dump({"streaming": {
        "body": {"text_color": color, "text_align": "center"},
        "reasoning": {"text_color": color, "text_size": "heading"},
        "reasoning_title": {"text_color": color},
        "tool_title": {"text_size": "heading"},
        "process": {"icon_color": color},
        "clarify": {"pending_template": color, "completed_template": color},
    }}))


def test_segment_actions_hold_card_style_through_finalization(tmp_path):
    configure(tmp_path, "blue")
    cfg = Config(tmp_path)
    first = cfg.card_snapshot()
    state = SegmentState()
    seg = state._new_reasoning("Reason")
    seg.elapsed_ms = 1000
    original = build_add_segment_action(seg, [], reasoning_timeline=True, config=first)
    configure(tmp_path, "green")
    assert build_add_segment_action(seg, [], reasoning_timeline=True, config=first) == original
    current = build_add_segment_action(seg, [], reasoning_timeline=True, config=cfg.card_snapshot())
    assert current != original
    finalized = build_reasoning_finalized_action(seg, reasoning_timeline=True, config=first)
    assert "blue" in finalized["params"]["partial_element"]["content"]
    panel_final = build_reasoning_finalized_action(seg, config=first)
    assert panel_final["params"]["partial_element"]["header"]["icon"]["color"] == "blue"
    tool = build_tool_update_action(element_id="tool_panel", steps=[{
        "name": "read", "title": "Read", "status": "running", "detail": "fixture",
        "output": "", "error": "", "icon": "read", "elapsed_ms": 0,
        "result_block": None, "error_block": None,
    }], config=first)
    assert tool["params"]["partial_element"]["elements"][0]["text"]["text_size"] == "heading"


@pytest.mark.asyncio
async def test_stream_and_recovery_use_owned_snapshot(tmp_path):
    configure(tmp_path, "blue")
    ctrl = StreamCardController(tmp_path)
    ctrl._client = AsyncMock()
    session = CardSession("style", "chat", asyncio.get_running_loop())
    session.render_config = ctrl._cfg.card_snapshot()
    session.card_id = "card"
    state = SegmentState()
    seg = state._new_reasoning("Reason")
    session.segment_state = state
    session.reasoning_timeline = True
    configure(tmp_path, "green")
    await ctrl._stream_or_update(session, "answer_0", "answer", kind="answer")
    payload = ctrl._client.cardkit_stream_element.call_args.args[2]
    assert "blue" in payload and "green" not in payload
    await ctrl._restore_reasoning_body(session, seg, "Reason")
    action = ctrl._client.cardkit_batch_update.call_args.args[1][0]
    body = action["params"]["elements"][0]
    assert body["text_size"] == "heading"
    assert body["content"] == ctrl._client.cardkit_stream_element.call_args.args[2]


@pytest.mark.asyncio
async def test_counts_and_budget_hints_retain_card_language(tmp_path):
    from hermes_fry_cards.cardkit import builder
    from hermes_fry_cards.cardkit.markdown import clamp_utf8
    from hermes_fry_cards.cardkit.styles import style_scope

    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"streaming": {"content_lang": "zh"}}))
    cfg = Config(tmp_path).card_snapshot()
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"streaming": {"content_lang": "en"}}))
    title = builder._process_title(1, 2, element_id="unified_panel_count", config=cfg)["content"]
    assert "已思考" in title
    with style_scope(cfg):
        assert "省略" in clamp_utf8("a" * 20000, max_bytes=10000)
    ctrl = StreamCardController(tmp_path)
    ctrl._client = AsyncMock()
    session = CardSession("lang", "chat", asyncio.get_running_loop())
    session.render_config = cfg
    session.set_card(card_id="card", card_msg_id="message")
    session.process_header_counts = (-1, -1)
    await ctrl._do_flush(session)
    assert any("已思考" in call.args[2] for call in ctrl._client.cardkit_stream_element.call_args_list)


def test_special_cards_use_sampled_configuration(tmp_path):
    configure(tmp_path, "blue")
    cfg = Config(tmp_path)
    first = cfg.card_snapshot()
    configure(tmp_path, "green")
    args = {"question": "Choose", "choices": ["A"], "clarify_id": "x", "multi_select": False}
    assert build_clarify_card(**args, config=first)["header"]["template"] == "blue"
    assert build_clarify_card(**args, config=cfg.card_snapshot())["header"]["template"] == "green"
    resolved = build_resolved_clarify_card(question="Choose", response_text="A", user_name="Me", config=first)
    assert resolved["header"]["template"] == "blue"
    interrupted = _interrupted_card(config=first)
    assert interrupted["config"]["style"]["color"]["cus-0"]


@pytest.mark.asyncio
async def test_clarify_callbacks_keep_initial_snapshot(tmp_path, monkeypatch):
    from hermes_fry_cards import clarify

    configure(tmp_path, "blue")
    monkeypatch.setattr(clarify, "Config", lambda: Config(tmp_path))
    gateway = SimpleNamespace(
        _lock=threading.Lock(), _entries={}, mark_awaiting_text=lambda value: True,
        resolve_gateway_clarify=lambda *args: True,
    )
    import tools
    monkeypatch.setattr(tools, "clarify_gateway", gateway, raising=False)
    monkeypatch.setitem(sys.modules, "tools.clarify_gateway", gateway)
    monkeypatch.setattr(clarify, "_callback_card", lambda self, card, **kwargs: card)
    adapter = SimpleNamespace(
        _client=True, _hl_SendResult=SimpleNamespace,
        _feishu_send_with_retry=AsyncMock(return_value={}),
        _finalize_send_result=lambda response, message: SimpleNamespace(success=True, message_id="out"),
        _is_interactive_operator_authorized=lambda user: True,
        _get_cached_sender_name=lambda user: "Me", resume_typing_for_chat=lambda chat: None,
    )
    identity = "style-lifecycle"
    try:
        result = await clarify.send_clarify(adapter, "chat", "Choose", ["A"], identity, "key")
        assert result.success
        assert clarify._CLARIFY_STATE[identity]["render_config"].style_options("clarify")["pending_template"] == "blue"
        configure(tmp_path, "green")
        event = SimpleNamespace(operator=SimpleNamespace(open_id="me"),
                                context=SimpleNamespace(open_chat_id="chat"))
        for action in ["other", "choose"]:
            card = clarify.handle_clarify_card_action(adapter, event=event, loop=None,
                action_value={"clarify_id": identity, "hermes_clarify_action": action, "idx": 0})
            assert card["header"]["template"] == "blue"
    finally:
        clarify._CLARIFY_STATE.pop(identity, None)
        clarify._CLARIFY_SELECTIONS.pop(identity, None)
