"""Reasoning writes preserve content across missing nodes and in-flight updates."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from hermes_fry_cards.cardkit.builder import build_streaming_card_v2
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.feishu import FeishuAPIError
from hermes_fry_cards.streaming.session import CardSession, SessionState


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


class MemoryCard:
    """Apply the actual action payloads to a card tree and reject missing IDs."""

    def __init__(self):
        self.card = build_streaming_card_v2(show_tool_use=False, show_reasoning=True)
        self.calls = []
        self.during_stream = None
        self.repair_error = None

    def node(self, ident):
        for obj in walk(self.card):
            if obj.get("element_id") == ident:
                return obj
        raise FeishuAPIError(f"not find elementID : {ident};", code=300313)

    def remove(self, ident):
        for obj in walk(self.card):
            for value in obj.values():
                if isinstance(value, list):
                    value[:] = [item for item in value if not isinstance(item, dict) or item.get("element_id") != ident]

    async def cardkit_batch_update(self, card_id, actions, *, sequence):
        self.calls.append(("batch", deepcopy(actions)))
        if self.repair_error and any(a.get("params", {}).get("type") == "insert_after" for a in actions):
            raise self.repair_error
        for action in actions:
            params = action["params"]
            if action["action"] == "partial_update_element":
                self.node(params["element_id"]).update(deepcopy(params["partial_element"]))
            elif action["action"] == "add_elements":
                target = params["target_element_id"]
                self.node(target)
                items = params["elements"]
                for item in walk(items):
                    ident = item.get("element_id")
                    if ident:
                        try:
                            self.node(ident)
                        except FeishuAPIError:
                            pass
                        else:
                            raise FeishuAPIError(f"Duplicate ID (ElementID {ident}: Code 1001)", code=300301)
                for obj in walk(self.card):
                    for value in obj.values():
                        if not isinstance(value, list):
                            continue
                        for index, item in enumerate(value):
                            if isinstance(item, dict) and item.get("element_id") == target:
                                pos = index + (params["type"] == "insert_after")
                                value[pos:pos] = deepcopy(items)
                                break

    async def cardkit_stream_element(self, card_id, element_id, content, *, sequence):
        self.calls.append(("stream", element_id, content))
        node = self.node(element_id)
        if self.during_stream:
            callback, self.during_stream = self.during_stream, None
            callback()
        node["content"] = content


async def setup(tmp_path):
    ctrl = StreamCardController(tmp_path)
    config = {"streaming": {"enabled": True}, "display": {"show_reasoning": True, "show_tool_use": False}}
    ctrl._cfg._reload = lambda: config
    ctrl._cfg._load = lambda: config
    ctrl._initialized = True
    ctrl._client = MemoryCard()
    session = CardSession("reasoning-test", "test-chat", asyncio.get_running_loop())
    session.set_card(card_id="test-card", card_msg_id="test-message")
    session.state = SessionState.STREAMING
    session.element_count = 5
    ctrl._configure_reasoning_timeline(session)
    return ctrl, session, config


@pytest.mark.asyncio
async def test_missing_reasoning_body_is_recreated_under_existing_title(tmp_path):
    ctrl, session, _ = await setup(tmp_path)
    state = session.segment_state
    state.on_reasoning_delta("accumulated reasoning")
    await ctrl._do_flush(session)
    seg = state.segments[0]
    title = deepcopy(ctrl._client.node(seg.el_id))
    count = session.element_count
    ctrl._client.remove(seg.text_el_id)
    state.on_reasoning_delta(" and continuation")
    await ctrl._do_flush(session)
    assert ctrl._client.node(seg.text_el_id)["content"] == seg.text
    assert ctrl._client.node(seg.el_id) == title
    assert session.element_count == count
    assert not seg.dirty


@pytest.mark.asyncio
async def test_hidden_stage_is_created_when_reasoning_is_shown(tmp_path):
    ctrl, session, config = await setup(tmp_path)
    state = session.segment_state
    state.on_reasoning_delta("retained reasoning")
    config["display"]["show_reasoning"] = False
    await ctrl._do_flush(session)
    seg = state.segments[0]
    assert not seg.created
    config["display"]["show_reasoning"] = True
    state.on_reasoning_delta(" after show")
    await ctrl._do_flush(session)
    assert ctrl._client.node(seg.text_el_id)["content"] == seg.text


@pytest.mark.asyncio
async def test_hidden_created_stage_receives_no_stream_writes(tmp_path):
    ctrl, session, config = await setup(tmp_path)
    state = session.segment_state
    state.on_reasoning_delta("before hide")
    await ctrl._do_flush(session)
    ctrl._client.calls.clear()
    config["display"]["show_reasoning"] = False
    # A structural batch can make old segments dirty while awaiting its write.
    original = ctrl._client.cardkit_batch_update
    async def dirty_during_batch(*args, **kwargs):
        await original(*args, **kwargs)
        state.segments[0].dirty = True
    ctrl._client.cardkit_batch_update = dirty_during_batch
    state.on_tool_event(1)
    await ctrl._do_flush(session)
    assert not [call for call in ctrl._client.calls if call[0] == "stream"]


@pytest.mark.asyncio
async def test_reasoning_arriving_during_write_stays_dirty(tmp_path):
    ctrl, session, _ = await setup(tmp_path)
    state = session.segment_state
    state.on_reasoning_delta("initial")
    ctrl._client.during_stream = lambda: state.on_reasoning_delta(" late")
    await ctrl._do_flush(session)
    assert state.segments[0].dirty
    await ctrl._do_flush(session)
    assert ctrl._client.node(state.segments[0].text_el_id)["content"] == "initial late"
    assert not state.segments[0].dirty


@pytest.mark.asyncio
async def test_failed_body_repair_retains_text_for_next_flush(tmp_path):
    ctrl, session, _ = await setup(tmp_path)
    state = session.segment_state
    state.on_reasoning_delta("complete accumulated text")
    await ctrl._do_flush(session)
    seg = state.segments[0]
    ctrl._client.remove(seg.text_el_id)
    seg.dirty = True
    ctrl._client.repair_error = FeishuAPIError("temporary failure", code=300308)
    await ctrl._do_flush(session)
    assert seg.dirty
    assert seg.text == "complete accumulated text"
    ctrl._client.repair_error = None
    await ctrl._do_flush(session)
    assert ctrl._client.node(seg.text_el_id)["content"] == seg.text


@pytest.mark.asyncio
async def test_batch_missing_body_matches_reasoning_text_id(tmp_path):
    ctrl, session, _ = await setup(tmp_path)
    state = session.segment_state
    state.on_reasoning_delta("saved text")
    await ctrl._do_flush(session)
    seg = state.segments[0]
    ctrl._client.remove(seg.text_el_id)
    actions = [{"action": "partial_update_element", "params": {
        "element_id": seg.text_el_id, "partial_element": {"content": seg.text},
    }}]
    await ctrl._do_batch_update(session, state.segments, actions, set(), {}, [])
    await ctrl._do_flush(session)
    assert ctrl._client.node(seg.text_el_id)["content"] == seg.text
    assert seg.created


@pytest.mark.asyncio
@pytest.mark.parametrize("append_during_batch", [False, True])
async def test_new_stage_writes_only_new_and_changed_text(tmp_path, append_during_batch):
    ctrl, session, _ = await setup(tmp_path)
    state = session.segment_state
    assert state is not None
    assert isinstance(ctrl._client, MemoryCard)
    state.on_answer_delta("existing answer")
    await ctrl._do_flush(session)
    answer = state.segments[-1]
    original_batch = ctrl._client.cardkit_batch_update
    old = None

    for stage in range(20):
        state.on_reasoning_delta(f"stage {stage}\nsecond line")
        current = state.segments[-1]
        pending = old if append_during_batch else None

        async def batch_with_delta(*args, pending=pending, **kwargs):
            await original_batch(*args, **kwargs)
            if pending is not None:
                pending.text += " late"
                pending.dirty = True

        ctrl._client.cardkit_batch_update = batch_with_delta
        ctrl._client.calls.clear()
        await ctrl._do_flush(session)
        expected = [current.text_el_id]
        if pending is not None:
            expected.insert(0, pending.text_el_id)
        writes = [call[1] for call in ctrl._client.calls
                  if call[0] == "stream" and not call[1].endswith("_count")]
        assert writes == expected
        assert ctrl._client.node(answer.el_id)["content"] == answer.text
        for seg in state.segments:
            if seg.type.value == "reasoning":
                assert ctrl._client.node(seg.text_el_id)["content"] == seg.text
        ctrl._client.cardkit_batch_update = original_batch
        session.tool_use.record_start(f"tool{stage}")
        state.on_tool_event(stage + 1)
        await ctrl._do_flush(session)
        old = current
