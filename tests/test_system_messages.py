"""Ordinary Feishu sends use cards while retaining routing, editing and fallback."""
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest
import yaml

from hermes_fry_cards.config import Config
from hermes_fry_cards import system_messages as system


def test_standalone_system_titles_have_no_borrowed_cron_icon(tmp_path):
    from hermes_fry_cards.cardkit.builder import build_background_card, build_cron_card
    home = tmp_path / "notice-home"
    home.mkdir()
    (home / "config.yaml").write_text(yaml.safe_dump({"streaming": {
        "enabled": True, "header": {"enabled": True, "icon_cron": "img_cron_fixture"},
    }}))
    cfg = Config(home).card_snapshot()
    for card in (system.build_system_card("System text", cfg),
                 build_background_card("", "Background text", config=cfg)):
        assert "icon" not in card["header"]
    cron = build_cron_card("Cron text", task_name="Named task", config=cfg)
    assert cron["header"]["icon"]["img_key"] == "img_cron_fixture"


@dataclass
class Result:
    success: bool
    message_id: str | None = None
    error: str | None = None


async def marked_send(adapter, *args, **kwargs):
    with system.system_notice_scope():
        return await adapter.send(*args, **kwargs)


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    path = home / 'config.yaml'
    path.write_text(yaml.safe_dump({'streaming': {'enabled': True}}))
    monkeypatch.setattr(system, 'Config', lambda: Config(home))

    class Adapter:
        MAX_MESSAGE_LENGTH = 8000

        def __init__(self):
            self._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(
                patch=AsyncMock(return_value=SimpleNamespace(success=lambda: True)),
            ))))
            self._feishu_send_with_retry = AsyncMock(return_value=SimpleNamespace(success=lambda: True))
            self.plain = AsyncMock(return_value=Result(True, 'plain-id'))
            self.original_edit = AsyncMock(return_value=Result(True, 'original-id'))
            self._run_blocking = AsyncMock(return_value=SimpleNamespace(success=lambda: True))

        async def _feishu_send_with_retry(self, **kwargs):
            raise AssertionError('fixture should bind its mock on the instance')

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            return await self.plain(chat_id, content, reply_to=reply_to, metadata=metadata)

        async def edit_message(self, chat_id, message_id, content, *, finalize=False):
            return await self.original_edit(chat_id, message_id, content, finalize=finalize)

        def format_message(self, content):
            return content.strip()

        def truncate_message(self, content, limit):
            return [content[i:i + limit] for i in range(0, len(content), limit)]

        def _finalize_send_result(self, response, default):
            return Result(response.success(), 'sent-id', None if response.success() else default)

    assert system.apply_patch(Adapter, SendResult=Result)
    obj = Adapter()
    obj.path = path
    return obj


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [
    '⚠️ Hermes 正在关闭——你当前的任务将被中断。',
    '✨ 会话已重置！重新开始。\n\n◆ Session: `recorded-session`',
    '权限不足。', '忙碌中，请稍后。',
    '| 命令 | 说明 |\n| --- | --- |\n| /new | 重置 |',
])
async def test_system_text_uses_one_card_and_same_route(adapter, content):
    metadata = {'thread_id': 'thread', 'reply_to_message_id': 'source', '_interim_send': True}
    result = await marked_send(adapter, 'chat', content, reply_to='source', metadata=metadata)
    assert result.success and result.message_id == 'sent-id'
    call = adapter._feishu_send_with_retry.call_args.kwargs
    assert call['msg_type'] == 'interactive'
    assert call['metadata'] is metadata and call['reply_to'] == 'source' and call['chat_id'] == 'chat'
    card = json.loads(call['payload'])
    assert card['body']['elements'][0]['columns'][0]['elements'][0]['content'] == content
    assert 'header' not in card and 'streaming_mode' not in card['config']
    adapter.plain.assert_not_awaited()


@pytest.mark.asyncio
async def test_editable_stream_preview_stays_on_original(adapter):
    md = {'expect_edits': True, 'thread_id': 'thread'}
    assert (await marked_send(adapter, 'chat', 'stream preview', metadata=md)).message_id == 'plain-id'
    adapter._feishu_send_with_retry.assert_not_awaited()
    assert (await adapter.edit_message('chat', 'plain-id', 'next', finalize=True)).message_id == 'original-id'


@pytest.mark.asyncio
@pytest.mark.parametrize('streaming', [{'enabled': False}, {'chat_types': []}, {'chat_types': ['dm']}])
async def test_admission_gates_keep_original(adapter, streaming):
    adapter.path.write_text(yaml.safe_dump({'streaming': streaming}))
    await marked_send(adapter, 'chat', 'notice')
    adapter.plain.assert_awaited_once()
    adapter._feishu_send_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_declared_allowed_chat_uses_card(adapter):
    adapter.path.write_text(yaml.safe_dump({'streaming': {'chat_types': ['dm']}}))
    await marked_send(adapter, 'chat', 'notice', metadata={'chat_type': 'dm'})
    adapter._feishu_send_with_retry.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_rejection_falls_back_without_an_extra_successful_send(adapter):
    adapter._feishu_send_with_retry.return_value = SimpleNamespace(success=lambda: False)
    assert (await marked_send(adapter, 'chat', 'notice')).message_id == 'plain-id'
    adapter.plain.assert_awaited_once_with('chat', 'notice', reply_to=None, metadata=None)


@pytest.mark.asyncio
async def test_unknown_transport_outcome_does_not_duplicate(adapter):
    adapter._feishu_send_with_retry.side_effect = TimeoutError('read timed out')
    result = await marked_send(adapter, 'chat', 'notice')
    assert not result.success and 'timed out' in result.error
    adapter.plain.assert_not_awaited()


@pytest.mark.asyncio
async def test_split_message_preserves_each_existing_chunk(adapter):
    text = '中' * 12000
    await marked_send(adapter, 'chat', text)
    assert adapter._feishu_send_with_retry.await_count == 2
    contents = [json.loads(c.kwargs['payload'])['body']['elements'][0]['columns'][0]['elements'][0]['content']
                for c in adapter._feishu_send_with_retry.call_args_list]
    assert ''.join(contents) == text


@pytest.mark.asyncio
async def test_later_chunk_rejection_retries_only_unsent_content(adapter):
    adapter.MAX_MESSAGE_LENGTH = 4
    adapter._feishu_send_with_retry.side_effect = [
        SimpleNamespace(success=lambda: True), SimpleNamespace(success=lambda: False),
    ]
    result = await marked_send(adapter, 'chat', 'abcdefghijkl')
    assert result.success
    adapter.plain.assert_awaited_once_with('chat', 'efghijkl', reply_to=None, metadata=None)


@pytest.mark.asyncio
async def test_post_handle_edits_same_card_with_original_style(adapter):
    await marked_send(adapter, 'chat', 'before')
    adapter.path.write_text(yaml.safe_dump({'streaming': {'body': {'light_color': '#FFFFFF'}}}))
    result = await adapter.edit_message('chat', 'sent-id', 'after')
    assert result.success and result.message_id == 'sent-id'
    request = adapter._run_blocking.call_args.args[1]
    card = json.loads(request.request_body.content)
    assert request.message_id == 'sent-id'
    assert card['body']['elements'][0]['columns'][0]['elements'][0]['content'] == 'after'
    assert card['config']['style']['color']['cus-0']['light_mode'] != 'rgba(255,255,255,1)'
    adapter.original_edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_chat_edit_does_not_take_over_card(adapter):
    await marked_send(adapter, 'chat', 'before')
    await adapter.edit_message('other-chat', 'sent-id', 'after')
    adapter.original_edit.assert_awaited_once()
    adapter._run_blocking.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata', [None, {'notify': True}, {'_interim_send': True}])
async def test_unmarked_final_and_progress_keep_original(adapter, metadata):
    with system.system_notice_scope(enabled=False):
        await adapter.send('chat', 'normal response', metadata=metadata)
    adapter.plain.assert_awaited_once()
    adapter._feishu_send_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_producer_scope_only_marks_command_events(adapter):
    class Producer:
        async def reply(self, event):
            return await adapter.send('chat', 'reply')

    assert system._scope_method(Producer, 'reply', commands_only=True)
    producer = Producer()
    with system.system_notice_scope(enabled=False):
        await producer.reply(SimpleNamespace(is_command=lambda: False, source=SimpleNamespace(chat_type='dm')))
        await producer.reply(SimpleNamespace(is_command=lambda: True, source=SimpleNamespace(chat_type='dm')))
    assert adapter.plain.await_count == 1 and adapter._feishu_send_with_retry.await_count == 1


@pytest.mark.asyncio
async def test_static_notice_producer_retains_binding_and_result():
    class Producer:
        @staticmethod
        async def send_notice(adapter, text):
            return system._notice_context.get(), adapter, text

    assert system._scope_method(Producer, 'send_notice')
    context, obj, text = await Producer.send_notice('adapter', 'notice')
    assert context is not None and obj == 'adapter' and text == 'notice'


@pytest.mark.asyncio
async def test_child_task_cannot_inherit_notice_delivery(adapter):
    import asyncio

    with system.system_notice_scope():
        await asyncio.create_task(adapter.send('chat', 'background ordinary response'))
    adapter.plain.assert_awaited_once()
    adapter._feishu_send_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_upstream_text_preserved_with_body_decoration(adapter):
    adapter.path.write_text(yaml.safe_dump({'streaming': {'body': {'bold': True, 'text_color': 'blue'}}}))
    text = '原文\n' * 1900
    await marked_send(adapter, 'chat', text)
    payload = json.loads(adapter._feishu_send_with_retry.call_args.kwargs['payload'])
    assert payload['body']['elements'][0]['columns'][0]['elements'][0]['content'] == text.strip()


def test_patch_is_idempotent_and_does_not_touch_rich_methods(adapter):
    cls = type(adapter)
    sender, editor = cls.send, cls.edit_message
    assert system.apply_patch(cls, SendResult=Result)
    assert cls.send is sender and cls.edit_message is editor
