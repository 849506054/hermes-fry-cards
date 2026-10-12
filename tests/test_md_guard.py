"""Markdown 防爆引擎测试 — fence 感知扫描、无损表格压缩、字节级内容预算."""

from __future__ import annotations

import pytest

from hermes_fry_cards.cardkit import i18n as _i18n_mod
from hermes_fry_cards.cardkit.markdown import (
    _ANSWER_MAX_BYTES,
    _downgrade_tables,
    _find_tables_outside_code_blocks,
    _scan_markdown_blocks,
    clamp_utf8,
    prepare_streaming_markdown,
    truncate_unclosed_markdown,
)
from hermes_fry_cards.feishu import (
    CARDKIT_FREQUENCY_LIMITED,
    CARDKIT_RATE_LIMITED,
    CARDKIT_TRANSIENT_ERROR_CODES,
)

TABLE = "| A | B |\n|---|---|\n| 1 | 2 |"


# --- 块扫描器 ---


class TestScanMarkdownBlocks:
    def test_table_recognized(self) -> None:
        blocks = _scan_markdown_blocks(TABLE)
        assert [b.kind for b in blocks] == ["table"]

    def test_unclosed_fence_till_end(self) -> None:
        text = "```\n| A | B |\n|---|---|\n| 1 | 2 |\n"
        assert _find_tables_outside_code_blocks(text) == []

    def test_inline_code_pipe_not_split(self) -> None:
        row = "| `a|b` | C |"
        table = f"{row}\n|---|---|\n| 1 | 2 |"
        results = _find_tables_outside_code_blocks(table)
        assert len(results) == 1
        assert "`a|b`" in results[0][2]

    def test_ragged_row_not_table(self) -> None:
        # 表头 2 列，分隔行 3 列 → 列数不一致，不识别为表格
        text = "| A | B |\n|---|---|---|\n| 1 | 2 |"
        assert _find_tables_outside_code_blocks(text) == []

    def test_mixed_content_offsets(self) -> None:
        text = f"前文\n\n{TABLE}\n\n后文"
        blocks = _scan_markdown_blocks(text)
        table = next(b for b in blocks if b.kind == "table")
        # 末行保留行尾换行，其余与原表格一致
        assert text[table.start : table.end].rstrip("\n") == TABLE


# --- 表格无损压缩 ---


class TestTableCompaction:
    def test_within_limit_untouched(self) -> None:
        text = "\n\n".join([TABLE] * 5)
        assert _downgrade_tables(text) == text

    def test_overflow_compacted(self) -> None:
        text = "\n\n".join([TABLE] * 6)
        result = _downgrade_tables(text)
        assert result.count("| A | B |") == 5
        assert "Table 6 · Row 1" in result
        assert "- A: 1" in result

    def test_multi_row_table(self) -> None:
        table = "| 名 | 值 |\n|---|---|\n| x | 1 |\n| y | 2 |"
        text = "\n\n".join([table] * 6)
        result = _downgrade_tables(text)
        assert "Table 6 · Row 1" in result
        assert "Table 6 · Row 2" in result
        assert "- 名: y" in result
        assert "- 值: 2" in result

    def test_notice_added_once(self) -> None:
        text = "\n\n".join([TABLE] * 8)
        result = _downgrade_tables(text)
        assert result.count("紧凑字段列表") == 1

    def test_no_tables_untouched(self) -> None:
        text = "普通段落，含 | 竖线但无表格。"
        assert _downgrade_tables(text) == text


# --- 字节级内容预算 ---


class TestClampUtf8:
    def test_under_budget_unchanged(self) -> None:
        text = "短文本" * 100
        assert clamp_utf8(text) == text

    def test_streaming_truncation(self) -> None:
        text = "汉" * 12000  # 36000 字节 > 18000
        result = clamp_utf8(text, preserve_tail=False)
        assert len(result.encode("utf-8")) <= _ANSWER_MAX_BYTES
        assert "已截断" in result

    def test_complete_tail_preserved(self) -> None:
        text = "头" * 4000 + "中" * 10000 + "尾结论"
        result = clamp_utf8(text, max_bytes=6000, preserve_tail=True)
        assert len(result.encode("utf-8")) <= 6000
        assert result.startswith("头")
        assert result.endswith("尾结论")
        assert "中段分析已省略" in result

    def test_multibyte_boundary_no_mojibake(self) -> None:
        text = "汉" * 12000
        result = clamp_utf8(text, preserve_tail=False)
        result.encode("utf-8")  # 若截断到多字节字符中间，这里会抛 UnicodeEncodeError

    def test_exact_budget_passes_through(self) -> None:
        text = "a" * _ANSWER_MAX_BYTES
        assert clamp_utf8(text) == text


# --- 瞬态错误码 ---


class TestTransientErrorCodes:
    def test_rate_limit_codes_included(self) -> None:
        assert CARDKIT_RATE_LIMITED in CARDKIT_TRANSIENT_ERROR_CODES
        assert CARDKIT_FREQUENCY_LIMITED in CARDKIT_TRANSIENT_ERROR_CODES


# --- 内容层文案语言（streaming.content_lang）---


class TestContentLangSelection:
    def test_zh_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_i18n_mod, "_CONTENT_LANG_CACHE", "zh")
        result = clamp_utf8("汉" * 12000, preserve_tail=False)
        assert "已截断" in result

    def test_en_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_i18n_mod, "_CONTENT_LANG_CACHE", "en")
        result = clamp_utf8("汉" * 12000, preserve_tail=False)
        assert "truncated" in result
        assert "已截断" not in result

    def test_en_notice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_i18n_mod, "_CONTENT_LANG_CACHE", "en")
        text = "\n\n".join([TABLE] * 6)
        result = _downgrade_tables(text)
        assert "compact field lists" in result
        assert "紧凑字段列表" not in result


# --- 截断未闭合 Markdown 标记与流式流水线 ---


class TestTruncateUnclosedMarkdown:
    def test_empty_text(self) -> None:
        assert truncate_unclosed_markdown("") == ""

    def test_closed_elements_remain_untouched(self) -> None:
        text = "Hello **world** with `code` and\n```python\nprint(1)\n```\nAll closed."
        assert truncate_unclosed_markdown(text) == text

    def test_unclosed_fenced_code_block(self) -> None:
        text = "Prefix\n```python\ndef foo():\n    pass"
        # 奇数个 ```，截断至最后一个 ```
        assert truncate_unclosed_markdown(text) == "Prefix\n"

    def test_unclosed_inline_code(self) -> None:
        text = "Normal text with `partial code"
        assert truncate_unclosed_markdown(text) == "Normal text with "

    def test_unclosed_bold(self) -> None:
        text = "Text with **bold text in progress"
        assert truncate_unclosed_markdown(text) == "Text with "

    def test_trailing_single_asterisk_truncated(self) -> None:
        text = "Text with single asterisk *"
        assert truncate_unclosed_markdown(text) == "Text with single asterisk "

    def test_trailing_double_asterisk_truncated(self) -> None:
        text = "Text ending in **"
        assert truncate_unclosed_markdown(text) == "Text ending in "


class TestPrepareStreamingMarkdown:
    def test_pipeline_integrates_truncation_and_caching(self) -> None:
        raw = "# Heading 1\n\nSome text with **broken bold"
        res1 = prepare_streaming_markdown(raw)
        assert "#### Heading 1" in res1
        assert "**broken bold" not in res1
        # LRU 缓存测试：多次调用同一实例返回相同内容
        res2 = prepare_streaming_markdown(raw)
        assert res1 == res2

