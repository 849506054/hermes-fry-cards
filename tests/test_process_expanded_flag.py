"""streaming.process_expanded 是热生效开关：改配置后新卡按新值建. """
from hermes_fry_cards.cardkit.builder import body_content_elements

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from hermes_fry_cards.cardkit.builder import (
    PROCESS_PANEL_ELEMENT_ID,
    TOOL_PANEL_ELEMENT_ID,
    build_streaming_card_v2,
)
from hermes_fry_cards.config import Config
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.streaming.session import CardSession, SessionState


def frames(card):
    return {e.get("element_id"): e for e in body_content_elements(card) if e["tag"] == "collapsible_panel"}


@pytest.mark.parametrize("expanded", [False, True])
def test_builder_honors_expanded_flag(expanded):
    card = build_streaming_card_v2(show_reasoning=True, show_tool_use=True,
                                   show_streaming_element=False, process_expanded=expanded)
    panels = frames(card)
    assert panels[PROCESS_PANEL_ELEMENT_ID]["expanded"] is expanded
    assert panels[TOOL_PANEL_ELEMENT_ID]["expanded"] is expanded


def test_default_follows_original_design():
    card = build_streaming_card_v2(show_reasoning=True, show_tool_use=False,
                                   show_streaming_element=False)
    assert frames(card)[PROCESS_PANEL_ELEMENT_ID]["expanded"] is True


def test_config_reads_flag_without_restart(tmp_path):
    cfg = Config(home=tmp_path)
    (tmp_path / "config.yaml").write_text("streaming:\n  process_expanded: true\n", encoding="utf-8")
    assert cfg.process_expanded is True
    (tmp_path / "config.yaml").write_text("streaming:\n  process_expanded: false\n", encoding="utf-8")
    assert cfg.process_expanded is False
    (tmp_path / "config.yaml").write_text("streaming: {}\n", encoding="utf-8")
    assert cfg.process_expanded is True


def make_ctrl(expanded):
    ctrl = StreamCardController()
    raw = {"streaming": {"enabled": True, "process_expanded": expanded},
           "feishu": {"app_id": "fixture"}}
    ctrl._cfg._reload = lambda: raw
    ctrl._client = AsyncMock()
    ctrl._client.cardkit_create.return_value = "card-new"
    ctrl._client.reply_card_by_id.return_value = "msg-new"
    return ctrl, raw


@pytest.mark.asyncio
@pytest.mark.parametrize("expanded", [False, True])
async def test_runtime_uses_config_value(expanded):
    ctrl, raw = make_ctrl(expanded)
    session = CardSession("flag-fixture", "chat", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    ctrl._sessions[session.message_id] = session
    assert await ctrl._create_streaming_card(session)
    card = ctrl._client.cardkit_create.call_args.args[0]
    panel = frames(card)[PROCESS_PANEL_ELEMENT_ID]
    assert panel["expanded"] is expanded
    # 改配置后同一进程内再建卡，跟着新值走
    raw["streaming"]["process_expanded"] = not expanded
    session.card_id = session.card_msg_id = ""
    assert await ctrl._create_streaming_card(session)
    card = ctrl._client.cardkit_create.call_args.args[0]
    assert frames(card)[PROCESS_PANEL_ELEMENT_ID]["expanded"] is (not expanded)
    json.dumps(card)
