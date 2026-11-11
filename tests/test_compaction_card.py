"""Context-compaction status lines collapse into one card per turn."""
import json

import pytest

from hermes_fry_cards import compaction_card
from hermes_fry_cards import system_messages as system
from tests.test_system_messages import Result, adapter  # noqa: F401  (shared fixture)

START = "🗜️ Compacting context — summarizing earlier conversation so I can continue..."
HEARTBEAT = "🗜️ Compacting context — still summarizing earlier conversation so I can continue..."
DONE = "✓ Context compaction complete — continuing turn..."
RESULT = "🗜️ Compressed: 232 → 41 messages\nApprox request size: ~157,901 → ~40,062 tokens"


def test_classify_separates_compaction_from_other_status():
    assert compaction_card.classify(START) == "start"
    assert compaction_card.classify(HEARTBEAT) == "heartbeat"
    assert compaction_card.classify(DONE) == "done"
    assert compaction_card.classify(RESULT) == "result"
    assert compaction_card.classify("⚠️ Hermes 正在关闭") is None
    assert compaction_card.classify("") is None


def test_done_line_closes_the_turn_so_the_next_compaction_starts_a_new_card():
    cards = compaction_card.CompactionCards()
    first = cards.take("chat", START)
    cards.bind("chat", first, "msg-1")
    assert cards.take("chat", HEARTBEAT)["message_id"] == "msg-1"
    done = cards.take("chat", DONE)
    cards.bind("chat", done, "msg-1")
    assert done["final"] is True
    fresh = cards.take("chat", START)
    assert fresh is not first and fresh["message_id"] is None
    assert fresh["lines"] == [START]


def test_start_before_done_closes_the_stale_card():
    """没有等到完成行就再次开始：旧卡收尾，另起一张，不把两轮混在一起。"""
    cards = compaction_card.CompactionCards()
    first = cards.take("chat", START)
    cards.bind("chat", first, "msg-1")
    assert cards.take("chat", HEARTBEAT)["message_id"] == "msg-1"
    second = cards.take("chat", START)
    assert second is not first
    assert second["message_id"] is None
    assert second["lines"] == [START]
    assert first["final"] is True


@pytest.mark.asyncio
async def test_three_status_lines_land_in_one_card(adapter):  # noqa: F811
    results = [await adapter.send("chat", line) for line in (START, HEARTBEAT, DONE)]

    assert [r.success for r in results] == [True, True, True]
    # One card created, two in-place updates: three notices, one bubble.
    assert adapter._feishu_send_with_retry.await_count == 1
    assert adapter._run_blocking.await_count == 2
    adapter.plain.assert_not_awaited()

    first_card = json.loads(adapter._feishu_send_with_retry.call_args.kwargs["payload"])
    body = str(first_card)
    assert START in body

    last_request = adapter._run_blocking.await_args_list[-1].args[1]
    final_card = json.loads(last_request.request_body.content)
    rendered = json.dumps(final_card, ensure_ascii=False)
    for line in (START, HEARTBEAT, DONE):
        assert line in rendered


def test_manual_result_replaces_the_done_line_in_the_open_card():
    """手动 /compress 的结果顶掉完成行，统计留在同一张卡的最后。"""
    cards = compaction_card.CompactionCards()
    first = cards.take("chat", START)
    cards.bind("chat", first, "msg-1")
    cards.take("chat", HEARTBEAT)
    done = cards.take("chat", DONE)
    cards.bind("chat", done, "msg-1")
    result = cards.take("chat", RESULT)
    assert result is done
    assert result["message_id"] == "msg-1"
    assert result["final"] is True
    assert result["lines"][:2] == [START, HEARTBEAT]
    assert result["lines"][2:] == RESULT.splitlines()


def test_result_without_a_progress_card_opens_one():
    cards = compaction_card.CompactionCards()
    entry = cards.take("chat", RESULT)
    assert entry["message_id"] is None
    assert entry["final"] is True
    assert entry["lines"] == RESULT.splitlines()


@pytest.mark.asyncio
async def test_manual_compress_result_lands_in_the_same_bubble(adapter):  # noqa: F811
    for line in (START, HEARTBEAT, DONE):
        await adapter.send("chat", line)
    await adapter.send("chat", RESULT)

    assert adapter._feishu_send_with_retry.await_count == 1
    assert adapter._run_blocking.await_count == 3
    last_request = adapter._run_blocking.await_args_list[-1].args[1]
    rendered = json.dumps(json.loads(last_request.request_body.content), ensure_ascii=False)
    assert "Compressed: 232 → 41 messages" in rendered
    assert "Approx request size" in rendered
    assert DONE not in rendered


@pytest.mark.asyncio
async def test_other_status_text_keeps_the_plain_system_card(adapter):  # noqa: F811
    with system.system_notice_scope():
        await adapter.send("chat", "权限不足。")
    assert adapter._run_blocking.await_count == 0
    assert adapter._feishu_send_with_retry.await_count == 1
