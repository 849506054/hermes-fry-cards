"""300309 之后换卡：新卡重新走流式写入。

飞书约十分钟后关掉单张卡的流式模式；同一会话因元素上限换到新卡时，
新卡是新的流式会话，标记必须回到未关闭状态，否则后续增量只能走元素更新。
"""

import asyncio

from hermes_fry_cards.streaming.session import CardSession


def test_stream_closed_resets_when_card_is_replaced():
    async def run() -> None:
        session = CardSession("msg-old", "chat-1", asyncio.get_running_loop())
        session.stream_closed = True

        session.set_card(card_id="card-new", card_msg_id="msg-new")

        assert session.stream_closed is False

    asyncio.run(run())
