"""按钮卡（审批 / 选择 / 更新提示）改走 2.0 卡面：结构与回传值都不能变."""
import json

from hermes_fry_cards.button_cards import to_v2


def _v1_card(title="⚠️ 需要确认", template="orange", body="```\n<write to AGENTS.md>\n```",
             actions=None):
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"content": title, "tag": "plain_text"}, "template": template},
        "elements": [{"tag": "markdown", "content": body}]
        + ([{"tag": "action", "actions": actions}] if actions is not None else []),
    }


def _button(label, kind, value):
    return {"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": kind, "value": value}


def _buttons(card):
    """卡里所有按钮元素，按出现顺序."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("tag") == "button":
                found.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(card["body"])
    return found


def test_buttons_keep_their_callback_values():
    value = {"hermes_action": "approve_once", "approval_id": 7}
    card = to_v2(_v1_card(actions=[_button("✅ 允许一次", "primary", value)]))
    buttons = _buttons(card)
    assert len(buttons) == 1
    assert buttons[0]["behaviors"] == [{"type": "callback", "value": value}]
    assert buttons[0]["value"] == value


def test_title_stays_the_card_header_and_body_survives():
    card = to_v2(_v1_card(title="❓ 需要你选一个"))
    text = json.dumps(card, ensure_ascii=False)
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "❓ 需要你选一个"
    assert "❓ 需要你选一个" not in json.dumps(card["body"], ensure_ascii=False)
    assert "<write to AGENTS.md>" in text


def test_two_button_row():
    card = to_v2(_v1_card(actions=[
        _button("✅ 允许一次", "primary", {"hermes_action": "approve_once", "approval_id": 3}),
        _button("❌ 拒绝", "danger", {"hermes_action": "deny", "approval_id": 3}),
    ]))
    row = next(e for e in card["body"]["elements"][0]["columns"][0]["elements"]
               if e.get("tag") == "column_set")
    assert len(row["columns"]) == 2


def test_a_2_0_card_passes_through_untouched():
    card = {"schema": "2.0", "body": {"elements": []}}
    assert to_v2(card) is card


def test_configured_icon_replaces_the_leading_emoji():
    from hermes_fry_cards.button_cards import _strip_leading_symbols

    assert _strip_leading_symbols("⚠️ Hermes 想要运行一条命令") == "Hermes 想要运行一条命令"
    assert _strip_leading_symbols("❓ 需要你选一个") == "需要你选一个"
    assert _strip_leading_symbols("没有图标") == "没有图标"


def test_clarify_card_keeps_update_multi_and_every_option():
    actions = [
        {"tag": "button", "text": {"tag": "plain_text", "content": str(i)},
         "type": "primary" if i == 1 else "default",
         "value": {"hermes_clarify_action": str(i - 1), "clarify_id": "c1"}}
        for i in range(1, 4)
    ]
    v1 = _v1_card(title="❓ 选择", template="blue", body="选一个\n\n1. A\n2. B\n3. C", actions=actions)
    v1["config"]["update_multi"] = True
    card = to_v2(v1)
    assert card["schema"] == "2.0"
    assert card["config"]["update_multi"] is True
    assert [b["value"]["hermes_clarify_action"] for b in _buttons(card)] == ["0", "1", "2"]
    assert card["header"]["title"]["content"].endswith("选择")


ClassVarIcons = {"approval": "k_appr", "choice": "k_choice"}


def test_choice_card_takes_the_choice_icon_and_approval_keeps_its_own():
    class _Cfg:
        width_mode = "default"
        body_text_size = "normal_v2"
        header_template = "default"
        header_enabled = True
        header_icons = ClassVarIcons
        body_light_color = "#F5F6F7"
        body_dark_color = "#222222"

        def style_options(self, role):
            return {}

    choice = to_v2(_v1_card(title="❓ 需要你选一个"), config=_Cfg())["header"]["icon"]
    approval = to_v2(_v1_card(title="⚠️ 需要确认"), config=_Cfg())["header"]["icon"]
    assert choice["img_key"] == "k_choice"
    assert approval["img_key"] == "k_appr"


def test_patch_applied_twice_still_sends_once_and_converts():
    import asyncio

    from hermes_fry_cards import button_cards

    sent = []

    class Fake:
        async def _send_interactive_card(self, chat_id, card, metadata, failure_message, *,
                                         state_map=None, state_id=None, session_key=None, extra_state=None):
            sent.append(card)
            return "ok"

        @staticmethod
        def _card_response(card_data=None):
            return card_data

    button_cards.apply_patch(Fake)
    button_cards.apply_patch(Fake)   # 适配器模块被加载两次时的真实情形
    v1 = _v1_card(actions=[_button("好", "primary", {"a": 1})])
    asyncio.run(Fake()._send_interactive_card("c", v1, {}, "fail"))
    assert len(sent) == 1
    assert sent[0]["schema"] == "2.0"
