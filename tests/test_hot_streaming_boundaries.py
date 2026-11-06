"""Hot display changes share a flush boundary; card structure commits on adoption."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
import yaml

from hermes_fry_cards.cardkit.builder import (
    PROCESS_PANEL_ELEMENT_ID,
    TOOL_PANEL_ELEMENT_ID,
)
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.streaming.session import CardSession, SessionState


def write_config(home, *, reasoning=False, tools=False, limit=3, border=False):
    (home / "config.yaml").write_text(yaml.safe_dump({
        "streaming": {"enabled": True, "process_border": border},
        "display": {
            "show_reasoning": reasoning, "show_tool_use": tools,
            "max_reasoning_panels": limit,
        },
        "feishu": {"app_id": "fixture"},
    }))


async def create(home):
    ctrl = StreamCardController(home)
    ctrl._initialized = True
    ctrl._client = AsyncMock()
    ctrl._client.cardkit_create.side_effect = ["old-card", "new-card"]
    ctrl._client.reply_card_by_id.side_effect = ["old-message", "new-message"]
    ctrl._schedule_flush = lambda session: None
    session = CardSession("boundary-fixture", "chat-fixture", asyncio.get_running_loop())
    ctrl._register_session(session, session_key=session.message_id)
    await ctrl._do_create_card(session)
    assert session.state == SessionState.STREAMING
    return ctrl, session


def ids(tree):
    if isinstance(tree, dict):
        found = {tree["element_id"]} if "element_id" in tree else set()
        return found.union(*(ids(value) for value in tree.values()))
    if isinstance(tree, list):
        return set().union(*(ids(value) for value in tree))
    return set()


def structure(session):
    return session.reasoning_timeline, session.segment_state.max_reasoning_panels


@pytest.mark.asyncio
@pytest.mark.parametrize("visible", ["reasoning", "tools"])
async def test_enable_during_flush_waits_for_next_flush(tmp_path, visible):
    write_config(tmp_path)
    ctrl, session = await create(tmp_path)
    known = ids(ctrl._client.cardkit_create.call_args.args[0])
    writes = []
    missing = []
    changed = False
    session.segment_state.on_reasoning_delta("counted while hidden", visible=False)
    session.segment_state.add_notice("progress")

    async def batch(card_id, actions, **kwargs):
        nonlocal changed
        for action in actions:
            params = action["params"]
            target = params.get("target_element_id", params.get("element_id"))
            if target not in known:
                missing.append(target)
            known.update(ids(params.get("elements", [])))
        if not changed:
            changed = True
            write_config(tmp_path, **{visible: True}, border=True)

    async def stream(card_id, element_id, content, **kwargs):
        writes.append(element_id)
        if element_id not in known:
            missing.append(element_id)

    ctrl._client.cardkit_batch_update.side_effect = batch
    ctrl._client.cardkit_stream_element.side_effect = stream
    await ctrl._do_flush(session)
    assert changed
    assert writes == []
    assert missing == []
    await ctrl._do_flush(session)
    panel_id = PROCESS_PANEL_ELEMENT_ID if visible == "reasoning" else TOOL_PANEL_ELEMENT_ID
    assert f"{panel_id}_count" in writes
    assert panel_id in session.process_panels
    assert missing == []
    panel = next(
        element for call in ctrl._client.cardkit_batch_update.call_args_list
        for action in call.args[1] for element in action["params"].get("elements", [])
        if element.get("element_id") == panel_id
    )
    assert "border" not in panel
    assert session.render_config.process_border is False


@pytest.mark.asyncio
@pytest.mark.parametrize("visible", ["reasoning", "tools"])
async def test_disable_during_panel_creation_applies_on_next_flush(tmp_path, visible):
    write_config(tmp_path)
    ctrl, session = await create(tmp_path)
    write_config(tmp_path, **{visible: True})
    session.segment_state.on_reasoning_delta("visible reasoning")
    session.tool_use.record_start("fixture-tool")
    session.segment_state.on_tool_event(1)
    changed = False

    async def batch(card_id, actions, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            write_config(tmp_path)

    ctrl._client.cardkit_batch_update.side_effect = batch
    await ctrl._do_flush(session)
    panel_id = PROCESS_PANEL_ELEMENT_ID if visible == "reasoning" else TOOL_PANEL_ELEMENT_ID
    assert changed
    assert panel_id in session.process_panels
    writes = [call.args[1] for call in ctrl._client.cardkit_stream_element.call_args_list]
    assert f"{panel_id}_count" in writes
    visible_seg = next(seg for seg in session.segment_state.segments if seg.type == (
        "reasoning" if visible == "reasoning" else "tool"
    ))
    assert visible_seg.created
    assert not visible_seg.dirty
    before = ctrl._client.cardkit_stream_element.await_count
    session.segment_state.on_reasoning_delta("next reasoning")
    session.tool_use.record_start("next-tool")
    session.segment_state.on_tool_event(2)
    await ctrl._do_flush(session)
    assert ctrl._client.cardkit_stream_element.await_count == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cardkit_create", "reply_card_by_id"])
@pytest.mark.parametrize("old_reasoning,old_tools,new_reasoning,new_tools", [
    (False, True, True, False),
    (True, False, False, True),
])
async def test_failed_replacement_preserves_owned_structure(
    tmp_path, failure, old_reasoning, old_tools, new_reasoning, new_tools,
):
    write_config(tmp_path, reasoning=old_reasoning, tools=old_tools, limit=2)
    ctrl, session = await create(tmp_path)
    old_structure = structure(session)
    old_config = session.render_config
    old_panels = session.process_panels.copy()
    write_config(tmp_path, reasoning=new_reasoning, tools=new_tools, limit=7)
    getattr(ctrl._client, failure).side_effect = RuntimeError("fixture failure")
    assert await ctrl._create_streaming_card(session) is None
    assert structure(session) == old_structure
    assert session.render_config is old_config
    assert session.process_panels == old_panels
    assert session.card_id == "old-card"
    assert session.pending_render_config is None
    ctrl._client.cardkit_close_streaming.assert_not_called()
    session.segment_state.on_answer_delta("continued answer")
    await ctrl._do_flush(session)
    assert session.state == SessionState.STREAMING
    assert any(call.args[0] == "old-card" and call.args[2] == "continued answer"
               for call in ctrl._client.cardkit_stream_element.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("old_reasoning,old_tools,new_reasoning,new_tools,new_limit", [
    (False, True, True, False, None),
    (True, False, False, True, 7),
    (True, True, True, True, 7),
])
async def test_replacement_adopts_sampled_structure_at_set_card(
    tmp_path, old_reasoning, old_tools, new_reasoning, new_tools, new_limit,
):
    write_config(tmp_path, reasoning=old_reasoning, tools=old_tools, limit=2)
    ctrl, session = await create(tmp_path)
    old_structure = structure(session)
    old_config = session.render_config
    observed = []
    write_config(tmp_path, reasoning=new_reasoning, tools=new_tools, limit=7, border=True)

    async def reply(*args):
        observed.append(structure(session))
        return "new-message"

    ctrl._client.reply_card_by_id.side_effect = reply
    new_ids = await ctrl._create_streaming_card(session)
    assert new_ids
    assert observed == [old_structure]
    assert structure(session) == old_structure
    assert session.render_config is old_config
    await ctrl._seal_current_card(session, [])
    assert structure(session) == old_structure
    write_config(tmp_path, reasoning=old_reasoning, tools=old_tools, limit=11)
    session.set_card(card_id=new_ids[0], card_msg_id=new_ids[1])
    assert structure(session) == (new_reasoning, new_limit)
    assert session.render_config.process_border is True
    assert session.pending_render_config is None
    assert session.process_panels == {
        panel for panel, enabled in (
            (PROCESS_PANEL_ELEMENT_ID, new_reasoning), (TOOL_PANEL_ELEMENT_ID, new_tools),
        ) if enabled
    }


@pytest.mark.asyncio
async def test_clarify_keeps_adopted_structure_across_seal_await(tmp_path):
    write_config(tmp_path, reasoning=False, tools=True, limit=2)
    ctrl, session = await create(tmp_path)
    write_config(tmp_path, reasoning=True, tools=False, limit=7)

    async def seal(card_id, card, **kwargs):
        write_config(tmp_path, reasoning=False, tools=True, limit=11)

    ctrl._client.cardkit_update.side_effect = seal
    assert await ctrl._do_clarify_split(session)
    assert session.card_id == "new-card"
    assert structure(session) == (True, None)
    assert PROCESS_PANEL_ELEMENT_ID in session.process_panels
    assert session.split_index == 0
    assert session.element_count == ctrl._skeleton_element_count(session)


@pytest.mark.asyncio
async def test_split_within_flush_keeps_display_sample(tmp_path):
    write_config(tmp_path, reasoning=True, tools=True)
    ctrl, session = await create(tmp_path)
    known = {"old-card": ids(ctrl._client.cardkit_create.call_args.args[0])}
    missing = []
    changed = False
    session.element_count = 175
    session.segment_state.on_answer_delta("first answer")
    session.segment_state.on_reasoning_delta("following reasoning")

    async def batch(card_id, actions, **kwargs):
        nonlocal changed
        for action in actions:
            params = action["params"]
            target = params.get("target_element_id", params.get("element_id"))
            if target not in known[card_id]:
                missing.append(target)
            known[card_id].update(ids(params.get("elements", [])))
        if not changed:
            changed = True
            write_config(tmp_path)

    async def new_card(card):
        known["new-card"] = ids(card)
        return "new-card"

    async def stream(card_id, element_id, content, **kwargs):
        if element_id not in known[card_id]:
            missing.append(element_id)

    ctrl._client.cardkit_batch_update.side_effect = batch
    ctrl._client.cardkit_create.side_effect = new_card
    ctrl._client.cardkit_stream_element.side_effect = stream
    await ctrl._do_flush(session)
    assert changed
    assert session.card_id == "new-card"
    assert missing == []
    assert session.reasoning_timeline is True
    assert {PROCESS_PANEL_ELEMENT_ID, TOOL_PANEL_ELEMENT_ID} <= known["new-card"]
