"""Native bottom headers share counts; visibility controls details only."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from hermes_fry_cards.cardkit.builder import (
    PROCESS_PANEL_ELEMENT_ID,
    body_content_elements,
    TOOL_PANEL_ELEMENT_ID,
    build_complete_card,
    build_streaming_card_v2,
)
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.streaming.segment_helper import build_process_panel_header_action
from hermes_fry_cards.streaming.segments import SegmentState
from hermes_fry_cards.streaming.session import CardSession, SessionState


def panels(card):
    return [e for e in body_content_elements(card) if e['tag'] == 'collapsible_panel']


def running_counts(reasoning, tools, *, tool_mode=False):
    if tool_mode:
        return f'🔧 工具执行中 {tools} 步 · 已思考 {reasoning} 轮'
    return f'💭 已思考 {reasoning} 轮 · 工具执行中 {tools} 步'


def assert_header(header, reasoning, tools, *, streaming=True, tool_mode=False):
    assert header['position'] == ('bottom' if streaming else 'top')
    if streaming:
        assert header['icon_position'] == 'right'
        assert header['icon']['token'] == 'down-small-ccm_outlined'
        assert header['icon_expanded_angle'] == -180
    else:
        # 封卡统计框不再提供展开箭头。
        assert 'icon' not in header and 'icon_position' not in header
        assert 'icon_expanded_angle' not in header
    zh = header['title'].get('i18n_content', {}).get('zh_cn', header['title']['content'])
    if streaming:
        assert zh == running_counts(reasoning, tools, tool_mode=tool_mode)
    else:
        assert f'💭{reasoning}·🔧{tools}' in zh
        assert '已思考' not in zh and '执行中' not in zh


@pytest.mark.parametrize('reasoning,tools', [(True, False), (False, True), (True, True)])
def test_initial_native_headers(reasoning, tools):
    card = build_streaming_card_v2(show_reasoning=reasoning, show_tool_use=tools)
    for panel in panels(card):
        assert panel['expanded']
        assert_header(panel['header'], 0, 0, tool_mode=panel['element_id'] == TOOL_PANEL_ELEMENT_ID)
    assert 'callback' not in json.dumps(card)
    assert 'process_collapse' not in json.dumps(card)


@pytest.mark.parametrize('element_id', [PROCESS_PANEL_ELEMENT_ID, TOOL_PANEL_ELEMENT_ID])
def test_partial_header_keeps_native_bottom_position(element_id):
    action = build_process_panel_header_action(3, 7, element_id=element_id)
    assert_header(action['params']['partial_element']['header'], 3, 7,
                  tool_mode=element_id == TOOL_PANEL_ELEMENT_ID)
    assert 'expanded' not in action['params']['partial_element']


def test_stage_count_tracks_events_independently_of_panel_cap():
    state = SegmentState(max_reasoning_panels=1)
    for index in range(12):
        state.on_reasoning_delta('reason')
        state.on_reasoning_delta('ing')
        state.on_tool_event(index + 1)
    assert state.reasoning_count == 12
    assert sum(s.type == 'reasoning' for s in state.segments) == 1


def setup(reasoning, tools):
    ctrl = StreamCardController()
    raw = {'streaming': {'enabled': True}, 'feishu': {'app_id': 'fixture'},
           'display': {'platforms': {'feishu': {'show_reasoning': reasoning, 'show_tool_use': tools}}}}
    ctrl._cfg._raw = raw
    ctrl._cfg._reload = lambda: raw
    ctrl._client = AsyncMock()
    session = CardSession('counts-fixture', 'fixture-chat', asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.set_card(card_id='fixture-card', card_msg_id='fixture-msg')
    ctrl._configure_reasoning_timeline(session)
    ctrl._sessions[session.message_id] = session
    return ctrl, session


@pytest.mark.asyncio
@pytest.mark.parametrize('reasoning,tools', [(True, False), (False, True), (True, True)])
async def test_stream_and_final_separate_count_from_detail(reasoning, tools):
    ctrl, session = setup(reasoning, tools)
    ctrl.on_reasoning(message_id=session.message_id, text='THOUGHT_DETAILS')
    ctrl.on_tool_update(message_id=session.message_id, tool_name='terminal', status='started', detail='TOOL_DETAILS')
    ctrl.on_tool_update(message_id=session.message_id, tool_name='terminal', status='completed')
    ctrl.on_reasoning(message_id=session.message_id, text='SECOND_THOUGHT_DETAILS')
    await ctrl._do_flush(session)
    actions = [a for call in ctrl._client.cardkit_batch_update.call_args_list for a in call.args[1]]
    ids = [PROCESS_PANEL_ELEMENT_ID] if reasoning else []
    ids += [TOOL_PANEL_ELEMENT_ID] if tools else []
    for element_id in ids:
        counts = [call.args[2] for call in ctrl._client.cardkit_stream_element.call_args_list
                  if call.args[1] == f'{element_id}_count']
        assert counts[-1] == running_counts(2, 1, tool_mode=element_id == TOOL_PANEL_ELEMENT_ID)
        assert all('header' not in a['params'].get('partial_element', {}) for a in actions
                   if a.get('params', {}).get('element_id') == element_id)
    sent = json.dumps(actions) + str(ctrl._client.cardkit_stream_element.call_args_list)
    if not reasoning:
        assert 'THOUGHT_DETAILS' not in sent
    if not tools:
        assert 'TOOL_DETAILS' not in sent
    ctrl._apply_completion_payload(session=session, answer='FINAL', duration=30, model='', tokens=None, context=None)
    assert await ctrl._do_complete_card_inner(session)
    final = ctrl._client.cardkit_update.call_args.args[1]
    assert not panels(final)  # 本回合没有中途消息，封卡不留空详情框
    if not reasoning:
        assert 'THOUGHT_DETAILS' not in json.dumps(final)
    if not tools:
        assert 'TOOL_DETAILS' not in json.dumps(final)
        assert 'terminal' not in json.dumps(final)


@pytest.mark.asyncio
async def test_hidden_thinking_path_counts_and_empty_delta_does_not():
    ctrl, session = setup(False, True)
    ctrl.on_thinking(message_id=session.message_id, text='<think>HIDDEN</think>')
    ctrl.on_reasoning(message_id=session.message_id, text='')
    assert session.segment_state.reasoning_count == 1
    assert all('HIDDEN' not in s.text for s in session.segment_state.segments)


@pytest.mark.asyncio
async def test_rollover_retains_turn_counts():
    ctrl, session = setup(True, False)
    ctrl.on_reasoning(message_id=session.message_id, text='old reasoning')
    ctrl.on_tool_update(message_id=session.message_id, tool_name='terminal', status='started')
    session.split_index = len(session.segment_state.segments)
    session.set_card(card_id='next-card', card_msg_id='next-msg')
    ctrl.on_reasoning(message_id=session.message_id, text='new reasoning')
    await ctrl._do_flush(session)
    counts = [c.args[2] for c in ctrl._client.cardkit_stream_element.call_args_list
              if c.args[1] == f'{PROCESS_PANEL_ELEMENT_ID}_count']
    assert counts[-1] == running_counts(2, 1)


@pytest.mark.asyncio
async def test_clarify_rollover_keeps_full_turn_counts():
    ctrl, session = setup(True, True)
    ctrl._client.cardkit_create.return_value = 'next-card'
    ctrl._client.reply_card_by_id.return_value = 'next-msg'
    for _ in range(2):
        ctrl.on_reasoning(message_id=session.message_id, text='before clarification')
        ctrl.on_tool_update(message_id=session.message_id, tool_name='terminal', status='started')
        ctrl.on_tool_update(message_id=session.message_id, tool_name='terminal', status='completed')
        assert await ctrl._do_clarify_split(session)
    ctrl.on_reasoning(message_id=session.message_id, text='after clarification')
    ctrl.on_tool_update(message_id=session.message_id, tool_name='terminal', status='started')
    await ctrl._do_flush(session)
    counts = [c.args[2] for c in ctrl._client.cardkit_stream_element.call_args_list
              if c.args[1] == f'{PROCESS_PANEL_ELEMENT_ID}_count']
    assert counts[-1] == running_counts(3, 3)
    ctrl._apply_completion_payload(session=session, answer='FINAL', duration=30, model='', tokens=None, context=None)
    assert await ctrl._do_complete_card_inner(session)
    final = ctrl._client.cardkit_update.call_args.args[1]
    assert not panels(final)


@pytest.mark.asyncio
async def test_rollover_before_first_tool_creates_both_headers():
    ctrl, session = setup(True, True)
    ctrl.on_reasoning(message_id=session.message_id, text='only reasoning so far')
    ctrl._client.cardkit_create.return_value = 'next-card'
    ctrl._client.reply_card_by_id.return_value = 'next-msg'
    assert await ctrl._create_streaming_card(session)
    card = ctrl._client.cardkit_create.call_args.args[0]
    ids = {p.get('element_id') for p in panels(card)}
    assert ids == {PROCESS_PANEL_ELEMENT_ID, TOOL_PANEL_ELEMENT_ID}


def test_both_modes_hidden_have_no_process_panel():
    state = SegmentState()
    state.on_reasoning_delta('hidden')
    state.on_completion_answer('FINAL')
    card = build_complete_card(segments=state.segments, all_tool_steps=[], show_reasoning=False, show_tool_use=False)
    assert not panels(card)
    assert 'hidden' not in json.dumps(card)


def test_hidden_reasoning_is_filtered_from_interrupted_summary():
    state = SegmentState()
    state.on_reasoning_delta('PRIVATE_REASONING')
    card = build_complete_card(segments=state.segments, all_tool_steps=[], show_reasoning=False,
                               show_tool_use=True, is_aborted=True)
    assert 'PRIVATE_REASONING' not in json.dumps(card)


@pytest.mark.asyncio
@pytest.mark.parametrize('reasoning,tools', [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize('lang', ['zh', 'en'])
async def test_counts_write_addressable_text_and_preserve_header(reasoning, tools, lang, monkeypatch):
    from copy import deepcopy

    from hermes_fry_cards.cardkit import i18n
    from hermes_fry_cards.feishu import FeishuAPIError

    monkeypatch.setattr(i18n, '_CONTENT_LANG_CACHE', lang)
    ctrl, session = setup(reasoning, tools)
    assert session.segment_state is not None
    assert isinstance(ctrl._client, AsyncMock)
    card = build_streaming_card_v2(show_reasoning=reasoning, show_tool_use=tools)
    headers = {p['element_id']: deepcopy(p['header']) for p in panels(card)}
    targets = {f'{ident}_count' for ident in headers}
    for ident, header in headers.items():
        assert header['title']['element_id'] == f'{ident}_count'
        assert len(header['title']['element_id']) <= 20
        assert 'i18n_content' not in header['title']
        prefix = ('🔧 工具执行中' if lang == 'zh' else '🔧 Tools') if ident == TOOL_PANEL_ELEMENT_ID else (
            '💭 已思考' if lang == 'zh' else '💭 Thinking'
        )
        assert header['title']['content'].startswith(prefix)

    def apply_batch(cid, actions, *, sequence):
        for action in actions:
            params = action['params']
            if params.get('element_id') in headers:
                assert 'header' not in params.get('partial_element', {})

    ctrl._client.cardkit_batch_update.side_effect = apply_batch
    session.segment_state.on_reasoning_delta('body')
    session.tool_use.record_start('fixture-tool')
    session.segment_state.on_tool_event(1)
    await ctrl._do_flush(session)
    calls = ctrl._client.cardkit_stream_element.call_args_list
    assert {c.args[1] for c in calls if c.args[1] in targets} == targets
    for call in calls:
        if call.args[1] not in targets:
            continue
        tool_mode = call.args[1] == f'{TOOL_PANEL_ELEMENT_ID}_count'
        if lang == 'zh':
            expected = running_counts(1, 1, tool_mode=tool_mode)
        else:
            expected = '🔧 Tools 1 steps · Thinking 1 stages' if tool_mode else '💭 Thinking 1 stages · Tools 1 steps'
        assert call.args[2] == expected
    ctrl._client.reset_mock(side_effect=False)
    await ctrl._do_flush(session)
    assert not ctrl._client.cardkit_stream_element.call_args_list

    # A failed count write stays pending while the ordinary body still advances.
    async def fail_count(cid, eid, text, *, sequence):
        if eid in targets:
            raise FeishuAPIError('temporary failure', code=500)
    ctrl._client.cardkit_stream_element.side_effect = fail_count
    session.segment_state.on_reasoning_delta('next body')
    await ctrl._do_flush(session)
    assert session.process_header_counts == (1, 1)
    if reasoning:
        assert any(c.args[2] == 'next body' for c in ctrl._client.cardkit_stream_element.call_args_list)
    ctrl._client.cardkit_stream_element.side_effect = None
    await ctrl._do_flush(session)
    assert session.process_header_counts == (2, 1)

    # Server-closed streaming uses the existing text-only element update path.
    ctrl._client.reset_mock(side_effect=False)
    session.stream_closed = True
    session.segment_state.reasoning_count += 1
    await ctrl._do_flush(session)
    updates = [a for call in ctrl._client.cardkit_batch_update.call_args_list for a in call.args[1]]
    assert {a['params']['element_id'] for a in updates} == targets
    assert all(set(a['params']['partial_element']) == {'content'} for a in updates)
