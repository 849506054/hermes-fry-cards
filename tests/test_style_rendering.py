"""Role styling uses card snapshots and preserves native element contracts."""

from __future__ import annotations

import inspect
from copy import deepcopy

import pytest
import yaml

from hermes_fry_cards.cardkit import builder
from hermes_fry_cards.cardkit.markdown import optimize_markdown_style, prepare_streaming_markdown
from hermes_fry_cards.config import Config
from hermes_fry_cards.streaming.segments import SegmentState


@pytest.fixture
def config_factory(tmp_path):
    def make(**streaming):
        path = tmp_path / str(len(list(tmp_path.iterdir())))
        path.mkdir()
        (path / "config.yaml").write_text(yaml.safe_dump({"streaming": streaming}))
        return Config(path).card_snapshot()

    return make


def test_header_two_value_padding_expands_for_cardkit(config_factory):
    cfg = config_factory(header={"padding": "8px 12px"})
    card = builder.build_streaming_card_v2(config=cfg, header_enabled=True)
    assert card["header"]["padding"] == "8px 12px 8px 12px"
    assert cfg.style_options("header")["padding"] == "8px 12px"


def test_completed_title_keeps_its_existing_size_owner(config_factory):
    cfg = config_factory(process_title={"text_size": "heading", "text_color": "blue"})
    state = SegmentState()
    state._new_reasoning("Thought")
    state.on_answer_delta("Answer")
    card = builder.build_complete_card(
        segments=state.segments, all_tool_steps=[], reasoning_count=1,
        footer_data={"duration": 30}, panel_text_size="small", config=cfg,
    )
    panel = next(el for el in builder.body_content_elements(card)
                 if el.get("element_id") == "unified_panel")
    assert panel["header"]["text_size"] == "small"
    assert "text_size" not in panel["header"]["title"]
    assert panel["header"]["text_color"] == "blue"


@pytest.mark.parametrize("raw", [
    "> - item", "---", "> ```python\n> print(1)\n> ```", "Title\n===", "    indented code",
])
def test_color_preserves_markdown_structure(config_factory, raw):
    from hermes_fry_cards.cardkit.styles import style_scope, text_content

    with style_scope(config_factory(body={"text_color": "blue"})):
        output = text_content(raw, "body")
    if raw == "> - item":
        assert output.startswith("> - <font")
    elif raw.startswith("Title"):
        assert output.endswith("\n===")
    else:
        assert output == raw


@pytest.mark.parametrize(("role", "budget"), [("body", 18000), ("reasoning", 10000)])
def test_style_overhead_preserves_text_within_existing_budget(config_factory, role, budget):
    from hermes_fry_cards.cardkit.styles import style_scope, text_content

    raw = "a\n" * (budget // 2)
    with style_scope(config_factory(**{role: {"text_color": "blue", "bold": True}})):
        output = text_content(raw, role)
    assert len(output.encode()) <= budget
    assert output == raw


def test_scope_nested_exception_and_signature(config_factory):
    from hermes_fry_cards.cardkit.styles import option, style_scope, styled_builder

    first = config_factory(reasoning={"text_color": "blue"})
    second = config_factory(reasoning={"text_color": "red"})

    @styled_builder
    def read(*, config=None):
        return option("reasoning", "text_color")

    assert str(inspect.signature(read)) == "(*, config=None)"
    with style_scope(first):
        assert read() == "blue"
        with pytest.raises(ValueError), style_scope(second):
            assert read() == "red"
            raise ValueError
        assert read() == "blue"
    assert read(config=second) == "red"


def test_text_formatting_preserves_fences_i18n_and_plain_identity(config_factory):
    from hermes_fry_cards.cardkit.styles import style_scope, text_content, text_element

    cfg = config_factory(
        reasoning={"text_color": "blue", "bold": True, "italic": True},
        process_title={"text_color": "purple", "text_size": "normal_v2"},
    )
    raw = "Before\n```python\nprint('unchanged')\n```\nAfter"
    with style_scope(cfg):
        styled = text_content(raw, "reasoning")
        assert "```python\nprint('unchanged')\n```" in styled
        assert "<font color='blue'>" in styled
        assert text_content(styled, "reasoning") == styled
        node = text_element({"tag": "markdown", "content": "Hello", "i18n_content": {"zh_cn": "你好"}}, "reasoning")
        assert node["i18n_content"]["zh_cn"] == text_content("你好", "reasoning")
        title = builder._process_title(2, 3, element_id="unified_panel_count")
        assert title["tag"] == "plain_text" and len(title["element_id"]) <= 20
        assert title["text_color"] == "purple" and title["text_size"] == "normal_v2"
        assert "<font" not in title["content"]
    with style_scope(config_factory()):
        assert text_content(raw, "reasoning") == raw


def test_markdown_options_are_cache_keys_and_safety_survives(config_factory):
    from hermes_fry_cards.cardkit.styles import style_scope

    raw = "# Title\n\n\n\nBody\n![bad](https://example.test/x)"
    prepare_streaming_markdown.cache_clear()
    with style_scope(config_factory(markdown={"heading_mode": "preserve", "max_blank_lines": 3})):
        first = prepare_streaming_markdown(raw)
        assert first.startswith("# Title\n\n\n\nBody")
        assert "https://" not in first
    with style_scope(config_factory()):
        second = prepare_streaming_markdown(raw)
        assert second.startswith("#### Title\n\nBody")
        assert optimize_markdown_style("# Heading") == "#### Heading"
    assert prepare_streaming_markdown.cache_info().misses == 2


def test_body_header_loading_and_independent_cards(config_factory):
    cfg = config_factory(
        width_mode="fill",
        header={"enabled": True, "padding": "8px", "icon_token": "info_outlined", "icon_color": "blue"},
        body={
            "text_size": "heading",
            "text_align": "right",
            "text_color": "purple",
            "bold": True,
            "margin": "4px",
            "body_padding": "6px",
            "horizontal_spacing": "8px",
            "vertical_spacing": "4px",
            "horizontal_align": "center",
            "vertical_align": "bottom",
            "surface_padding": "8px",
            "surface_margin": "4px",
        },
        loading={"img_key": "img_fixture", "size": "24px 24px"},
    )
    card = builder.build_streaming_card_v2(header_enabled=True, config=cfg)
    header = card["header"]
    assert header["padding"] == "8px" and header["icon"]["token"] == "info_outlined"
    assert set(header["title"]) == {"tag", "content", "i18n_content"}
    assert card["body"]["padding"] == "6px"
    surface, *trailing = card["body"]["elements"]
    assert [element["element_id"] for element in trailing] == ["loading_cursor"]
    assert surface["columns"][0]["padding"] == "8px" and surface["margin"] == "4px"
    assert surface["columns"][0]["vertical_align"] == "bottom"
    assert surface["horizontal_spacing"] == "8px"
    assert surface["columns"][0]["vertical_spacing"] == "4px"
    assert surface["columns"][0]["horizontal_align"] == "center"
    content = builder.body_content_elements(card)
    assert content[0]["text_align"] == "right" and content[0]["margin"] == "4px"
    assert content[-1]["icon"] == {"tag": "custom_icon", "img_key": "img_fixture", "size": "24px 24px"}
    for independent in (
        builder.build_cron_card("Body", task_name="fixture", config=cfg),
        builder.build_background_card("Preview", "Body", config=cfg),
        builder.build_overflow_fallback_card(config=cfg),
    ):
        assert independent["config"]["width_mode"] == "fill"
        body = next(el for el in builder.body_content_elements(independent) if el["tag"] == "markdown")
        assert body["text_size"] == "heading" and body["text_align"] == "right"
    hidden = config_factory(header={"enabled": False})
    assert "header" not in builder.build_cron_card("Body", task_name="fixture", config=hidden)
    standalone = builder.build_background_card("Preview", "Body", config=cfg)
    assert standalone["header"]["title"]["content"] == "Background task"
    referenced = builder.build_background_card("Preview", "Body", config=cfg, has_reference=True)
    assert "header" not in referenced


def test_process_reasoning_summary_footer_and_status_borders(config_factory):
    from hermes_fry_cards.cardkit.styles import style_scope

    cfg = config_factory(
        process_border=True,
        process={
            "padding": "12px",
            "margin": "6px",
            "vertical_spacing": "12px",
            "horizontal_spacing": "8px",
            "horizontal_align": "center",
            "vertical_align": "bottom",
            "background_color": "grey",
            "border_color": "blue",
            "border_radius": "8px",
            "completed_color": "purple",
            "error_color": "orange",
            "stopped_color": "yellow",
            "header_padding": "4px",
            "header_background_color": "blue",
            "header_vertical_align": "bottom",
            "header_width": "fill",
            "icon_token": "right_outlined",
            "icon_color": "red",
            "icon_size": "20px 20px",
            "icon_position": "left",
            "icon_expanded_angle": 90,
            "arrow": True,
        },
        reasoning={"text_size": "normal_v2", "text_color": "blue", "text_align": "right", "margin": "4px"},
        reasoning_title={"text_size": "heading", "text_color": "purple", "bold": True},
        summary={"text_size": "normal_v2", "text_color": "orange", "italic": True},
        footer={"text_color": "blue", "error_color": "purple", "bold": True, "separator": " / ", "margin": "8px"},
    )
    with style_scope(cfg):
        panel = builder.build_streaming_process_pending_panel()
        assert panel["border"] == {"color": "blue", "corner_radius": "8px"}
        assert panel["padding"] == "12px" and panel["background_color"] == "grey"
        assert panel["header"]["padding"] == "4px" and panel["header"]["width"] == "fill"
        assert panel["header"]["position"] == "bottom" and panel["header"]["icon_position"] == "left"
        assert panel["header"]["icon_expanded_angle"] == 90
        title, text = builder._build_reasoning_stage("Reason", 10)
        assert title["text_size"] == "heading" and "color='purple'" in title["content"]
        assert text["text_size"] == "normal_v2" and "color='blue'" in text["content"]
        nested = builder._build_reasoning_panel("Reason")
        assert nested["header"]["title"]["tag"] == "plain_text"
        summary = builder._build_process_summary("Summary", zh_text="摘要")
        assert "color='orange'" in summary["content"] and "color='grey'" not in summary["content"]
        (footer,) = builder._build_footer_elements({"duration": 2}, is_error=True, fields=[["status", "elapsed"]])
        assert " / " in footer["content"] and "color='purple'" in footer["content"]
        assert footer["margin"] == "8px"
    state = SegmentState()
    state._new_reasoning("Reason")
    state.on_answer_delta("Answer")
    for status, kwargs in (("purple", {}), ("orange", {"is_error": True}), ("yellow", {"is_aborted": True})):
        complete = builder.build_complete_card(
            segments=state.segments, all_tool_steps=[], reasoning_count=1, show_tool_use=False, config=cfg, **kwargs
        )
        panel = next(e for e in builder.body_content_elements(complete) if e["tag"] == "collapsible_panel")
        assert panel["border"] == {"color": status, "corner_radius": "8px"}
        assert panel["header"]["position"] == "top"
    cfg = config_factory(process={"completed_border": False})
    complete = builder.build_complete_card(
        segments=state.segments, all_tool_steps=[], reasoning_count=1, show_tool_use=False, config=cfg
    )
    panel = next(e for e in builder.body_content_elements(complete) if e["tag"] == "collapsible_panel")
    assert "border" not in panel


def test_tool_roles_and_safe_output(config_factory):
    step = {
        "name": "terminal",
        "title": "Run shell",
        "status": "success",
        "detail": "safe detail",
        "icon": "tool_02",
        "result_block": {"content": "secret-free\n```literal", "language": "text"},
    }
    original = deepcopy(step)
    cfg = config_factory(
        tool_icons={"terminal": "code_outlined"},
        tool_title={
            "text_size": "normal_v2",
            "text_align": "right",
            "margin": "4px",
            "icon_color": "purple",
            "success_color": "orange",
            "text_color": "blue",
        },
        tool_detail={"text_size": "normal_v2", "text_color": "red", "text_align": "right", "margin": "6px"},
        tool_output={"text_size": "normal_v2", "text_color": "purple", "bold": True, "italic": True, "margin": "8px"},
    )
    title, detail, output = builder._build_tool_step_elements(step, config=cfg)
    assert title["icon"] == {"tag": "standard_icon", "token": "code_outlined", "color": "purple"}
    assert "color='orange'>Succeeded" in title["text"]["content"]
    assert title["margin"] == "4px" and title["text"]["text_size"] == "normal_v2"
    assert detail["text"]["tag"] == "plain_text" and detail["text"]["text_color"] == "red"
    assert detail["margin"] == "6px"
    assert "````text\nsecret-free\n```literal\n````" in output["text"]["content"]
    assert output["margin"] == "8px" and "color='purple'" in output["text"]["content"]
    assert step == original


def test_native_table_fields_and_root(config_factory):
    cfg = config_factory(
        table={
            "page_size": 5,
            "row_height": "middle",
            "row_max_height": "100px",
            "column_width": "120px",
            "horizontal_align": "center",
            "vertical_align": "top",
            "freeze_first_column": True,
            "margin": "8px",
            "header_text_align": "right",
            "header_text_size": "heading",
            "header_background_style": "grey",
            "header_text_color": "grey",
            "header_bold": False,
            "header_lines": 2,
        }
    )
    card = builder.build_cron_card("Before\n\n| A | B |\n|---|---|\n|1|2|", config=cfg)
    table = next(e for e in card["body"]["elements"] if e["tag"] == "table")
    assert table["page_size"] == 5 and table["row_height"] == "middle"
    assert table["row_max_height"] == "100px" and table["freeze_first_column"] is True
    assert table["margin"] == "8px"
    assert table["header_style"] == {
        "text_align": "right",
        "text_size": "heading",
        "background_style": "grey",
        "text_color": "grey",
        "bold": False,
        "lines": 2,
    }
    assert all(
        col["width"] == "120px" and col["horizontal_align"] == "center" and col["vertical_align"] == "top"
        for col in table["columns"]
    )


@pytest.mark.parametrize("role", ["body", "reasoning", "reasoning_title", "summary", "tool_output", "footer"])
def test_all_markdown_typography_fields_reach_semantic_consumers(role, config_factory):
    from hermes_fry_cards.cardkit.styles import style_scope, text_content

    cfg = config_factory(
        **{
            role: {
                "text_size": "heading",
                "text_align": "right",
                "text_color": "blue",
                "bold": True,
                "italic": True,
                "margin": "6px",
            }
        }
    )
    with style_scope(cfg):
        if role == "body":
            element = builder._streaming_element("Hello")
        elif role in {"reasoning", "reasoning_title"}:
            element = builder._build_reasoning_stage("Hello")[role == "reasoning"]
        elif role == "summary":
            element = builder._build_process_summary("Hello")
        elif role == "tool_output":
            element = builder._build_tool_step_output({"result_block": {"content": "code", "language": "text"}})
        else:
            (element,) = builder._build_footer_elements({"duration": 2}, fields=[["status", "elapsed"]])
        text = element.get("text", element)
        assert text["text_size"] == "heading" and text["text_align"] == "right"
        assert element["margin"] == "6px"
        assert "<font color='blue'>" in text["content"] and "***" in text["content"]
        assert text_content(text["content"], role) == text["content"]


@pytest.mark.parametrize("role", ["header", "process"])
def test_custom_icons_use_legal_native_shape(role, config_factory):
    cfg = config_factory(**{role: {"icon_token": "info_outlined", "icon_color": "red",
                                    "icon_img_key": "img_fixture", "arrow": True}})
    if role == "header":
        icon = builder._build_header("completed", config=cfg)["icon"]
    else:
        icon = builder.build_streaming_process_pending_panel(config=cfg)["header"]["icon"]
        assert icon["size"] == "16px 16px"
    assert icon["tag"] == "custom_icon" and icon["img_key"] == "img_fixture"
    assert "token" not in icon and "color" not in icon


@pytest.mark.parametrize("status", ["running", "success", "error", "other"])
def test_tool_status_colors_and_title_emphasis(status, config_factory):
    field = status if status != "other" else "unknown"
    cfg = config_factory(tool_title={field + "_color": "purple", "bold": True, "italic": True})
    step = {"title": "Title", "name": "terminal", "status": status}
    element = builder._build_tool_step_title(step, config=cfg)
    assert "<font color='purple'>" in element["text"]["content"]
    assert "***Title***" in element["text"]["content"]
    cfg = config_factory(tool_title={"bold": False})
    assert "**Title**" not in builder._build_tool_step_title(step, config=cfg)["text"]["content"]


def test_complete_count_style_and_plain_detail_alignment(config_factory):
    cfg = config_factory(
        process_title={"text_color": "purple", "text_size": "heading"}, tool_detail={"text_align": "center"}
    )
    state = SegmentState()
    state._new_reasoning("Reason")
    state.on_answer_delta("Answer")
    card = builder.build_complete_card(
        segments=state.segments, all_tool_steps=[], reasoning_count=1, show_tool_use=False, config=cfg
    )
    panel = next(e for e in builder.body_content_elements(card) if e.get("element_id") == "unified_panel")
    title = panel["header"]["title"]
    assert title["tag"] == "plain_text"
    assert panel["header"]["text_size"] == "notation"
    assert panel["header"]["text_color"] == "purple"
    detail = builder._build_tool_step_detail({"detail": "literal <tag> **text**"}, config=cfg)
    assert detail["text"]["text_align"] == "center"
    assert detail["text"]["content"] == "literal <tag> **text**"


@pytest.mark.parametrize("blank_lines", [1, 2, 3, 4])
def test_spacing_options_and_fence_content(blank_lines, config_factory):
    cfg = config_factory(markdown={"heading_mode": "preserve", "max_blank_lines": blank_lines})
    fenced = "```md\n# Literal\n\n\n\nCode\n```"
    text = "# Heading" + "\n" * 7 + "Body\n" + fenced
    result = optimize_markdown_style(text, config=cfg)
    assert result.startswith("# Heading" + "\n" * (blank_lines + 1) + "Body")
    assert fenced in result


def test_inline_formatting_is_idempotent_and_keeps_table_structure(config_factory):
    from hermes_fry_cards.cardkit.styles import style_scope, text_content

    cfg = config_factory(body={"bold": True, "italic": True, "text_color": "blue"})
    table = "| A | B |\n|---|---|\n|1|2|\n"
    with style_scope(cfg):
        for text in (
            "Before **bold** after",
            "**Title** · <font color='grey'>Status</font>",
            "# Heading\n- item\n`code`",
            "<font color='grey'>A</font> + <font color='red'>B</font>",
            "[link](https://example.test) and ![image](img_fixture)",
            table,
        ):
            styled = text_content(text, "body")
            assert text_content(styled, "body") == styled
        assert text_content(table, "body") == table


def _panel_of(card, element_id="unified_panel"):
    return next(el for el in builder.body_content_elements(card)
                if el.get("element_id") == element_id)


def test_running_panel_keeps_arrow_by_default(config_factory):
    card = builder.build_streaming_card_v2(config=config_factory(), show_reasoning=True)
    header = _panel_of(card)["header"]
    assert header["icon"]["token"] == "down-small-ccm_outlined"
    assert header["icon_expanded_angle"] == -180


def test_running_panel_arrow_can_be_disabled(config_factory):
    card = builder.build_streaming_card_v2(config=config_factory(process={"arrow": False}),
                                           show_reasoning=True)
    assert "icon" not in _panel_of(card)["header"]


def test_completed_panel_preserves_content_and_hides_arrow(config_factory):
    state = SegmentState()
    state._new_reasoning("Thought")
    state.on_answer_delta("Answer")
    card = builder.build_complete_card(
        segments=state.segments, all_tool_steps=[], reasoning_count=1,
        footer_data={"duration": 30}, config=config_factory(),
    )
    panel = _panel_of(card)
    assert "icon" not in panel["header"]




@pytest.mark.parametrize("status,field,zh", [
    ("streaming", "icon_processing", "处理中..."),
    ("completed", "icon_completed", "已完成"),
    ("stopped", "icon_stopped", "已停止"),
    ("error", "icon_error", "出错"),
])
def test_header_status_icon_replaces_emoji_prefix(status, field, zh, config_factory):
    cfg = config_factory(header={field: "img_v3_probe"})
    header = builder._build_header(status, config=cfg)
    assert header["icon"] == {"tag": "custom_icon", "img_key": "img_v3_probe"}
    assert header["title"]["i18n_content"]["zh_cn"] == zh


def test_header_without_icon_keeps_emoji_text(config_factory):
    header = builder._build_header("streaming", config=config_factory())
    assert "icon" not in header
    assert header["title"]["i18n_content"]["zh_cn"].startswith("💭")


def test_cron_image_changes_only_header_prefix_and_icon(config_factory):
    before = builder.build_cron_card("Body", task_name="fixture", config=config_factory())
    after = builder.build_cron_card(
        "Body", task_name="fixture", config=config_factory(header={"icon_cron": "img_v3_cron"}),
    )
    assert after["header"]["icon"] == {"tag": "custom_icon", "img_key": "img_v3_cron"}
    expected = deepcopy(before)
    expected["header"]["title"]["content"] = expected["header"]["title"]["content"].removeprefix(":Alarm: ")
    expected["header"]["icon"] = {"tag": "custom_icon", "img_key": "img_v3_cron"}
    assert after == expected
