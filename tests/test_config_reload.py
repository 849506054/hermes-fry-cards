"""Safe hot reload, per-card snapshots and language-aware markdown caching."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
import yaml

from hermes_fry_cards.cardkit import i18n
from hermes_fry_cards.cardkit.builder import build_streaming_card_v2
from hermes_fry_cards.cardkit.markdown import clamp_utf8, prepare_streaming_markdown
from hermes_fry_cards.config import HEADER_TEMPLATES, Config


def _write(home: Path, raw: dict | str) -> Path:
    path = home / "config.yaml"
    previous = path.stat().st_mtime_ns if path.exists() else 0
    text = raw if isinstance(raw, str) else yaml.safe_dump(raw)
    path.write_text(text, encoding="utf-8")
    # Guarantee metadata changes even on filesystems with coarse write timestamps.
    stamp = max(path.stat().st_mtime_ns, previous + 1)
    os.utime(path, ns=(stamp, stamp))
    return path


@pytest.mark.parametrize(
    ("key", "first", "second", "getter", "expected_first", "expected_second"),
    [
        ("enabled", True, False, "enabled", True, False),
        ("chat_types", ["dm"], ["group"], "chat_types", {"dm"}, {"group"}),
        ("card_ttl_sec", 100, 200, "card_duration_sec", 100, 200),
        ("width_mode", "compact", "fill", "width_mode", "compact", "fill"),
        ("panel_expanded", True, False, "panel_expanded", True, False),
        ("process_expanded", True, False, "process_expanded", True, False),
        ("process_border", False, True, "process_border", False, True),
        ("header", {"enabled": True}, {"enabled": False}, "header_enabled", True, False),
        ("header", {"min_duration": 1}, {"min_duration": 9}, "header_min_duration", 1.0, 9.0),
        ("header", {"template": "blue"}, {"template": "grey"}, "header_template", "blue", "grey"),
        ("footer", {"enabled": False}, {"enabled": True}, "footer_enabled", False, True),
        ("footer", {"show_label": False}, {"show_label": True}, "footer_show_label", False, True),
        ("footer", {"fields": ["model"]}, {"fields": ["status"]}, "footer_fields", [["model"]], [["status"]]),
        ("footer", {"text_size": "normal"}, {"text_size": "heading"}, "footer_text_size", "normal", "heading"),
        ("body", {"text_size": "normal"}, {"text_size": "heading"}, "body_text_size", "normal", "heading"),
        ("body", {"light_color": "#abcdef"}, {"light_color": "#123456"}, "body_light_color", "#ABCDEF", "#123456"),
        ("body", {"dark_color": "#123456"}, {"dark_color": "#abcdef"}, "body_dark_color", "#123456", "#ABCDEF"),
        ("content_lang", "zh", "en", "content_lang", "zh", "en"),
        ("completion_notice", False, True, "completion_notice", False, True),
        ("completion_notice_text", "first", "second", "completion_notice_text", "first", "second"),
        ("flush_interval_ms", 300, 700, "flush_interval_ms", 300, 700),
    ],
)
def test_same_config_reads_independent_writes(tmp_path, key, first, second, getter, expected_first, expected_second):
    cfg = Config(tmp_path)
    _write(tmp_path, {"streaming": {key: first}})
    assert getattr(cfg, getter) == expected_first
    snapshot = cfg.card_snapshot()
    _write(tmp_path, {"streaming": {key: second}})
    assert getattr(cfg, getter) == expected_second
    assert getattr(snapshot, getter) == expected_first
    assert getattr(cfg.card_snapshot(), getter) == expected_second


def test_same_builder_uses_second_read_and_snapshot_holds_first(tmp_path):
    cfg = Config(tmp_path)

    def build(config):
        return build_streaming_card_v2(
            header_enabled=config.header_enabled,
            width_mode=config.width_mode,
            text_size=config.body_text_size,
            show_tool_use=False,
        )

    _write(tmp_path, {"streaming": {"width_mode": "compact", "header": {"enabled": True}}})
    snapshot = cfg.card_snapshot()
    first = build(cfg)
    _write(tmp_path, {"streaming": {"width_mode": "fill", "header": {"enabled": False}}})
    second = build(cfg)
    assert first["config"]["width_mode"] == "compact"
    assert "header" in first
    assert second["config"]["width_mode"] == "fill"
    assert "header" not in second
    assert build(snapshot) == first


@pytest.mark.parametrize("bad", [
    "streaming: [", "[]", "null", "123", "false", "fixture-private-marker", "",
    "streaming: 2026-99-99", "streaming: !!float nope", "display: 2026-02-31",
])
def test_invalid_write_retains_last_valid_and_recovers(tmp_path, bad, caplog):
    cfg = Config(tmp_path)
    _write(tmp_path, {"streaming": {"enabled": False}, "display": {"show_reasoning": False}})
    assert cfg.enabled is False
    _write(tmp_path, bad)
    with patch("hermes_fry_cards.config.yaml.safe_load", wraps=yaml.safe_load) as parse:
        for _ in range(5):
            assert cfg.enabled is False
            assert cfg.show_reasoning is False
        assert parse.call_count == 1
    assert "fixture-private-marker" not in caplog.text
    assert len(caplog.records) == 1
    _write(tmp_path, {"streaming": {"enabled": True}, "display": {"show_reasoning": True}})
    assert cfg.enabled is True
    assert cfg.show_reasoning is True


def test_missing_file_defaults_then_keeps_last_valid(tmp_path):
    cfg = Config(tmp_path)
    assert cfg.enabled is True
    assert cfg.card_duration_sec == 600
    assert cfg.body_light_color == "#F5F6F7"
    assert cfg.body_dark_color == "#222222"
    assert cfg.process_border is False
    assert cfg.header_template == "default"
    assert cfg._reload() == {}
    path = _write(tmp_path, {"streaming": {"enabled": False}})
    assert cfg.enabled is False
    path.unlink()
    assert cfg.enabled is False
    _write(tmp_path, {})
    assert cfg.enabled is True


def test_bad_initial_file_recovers_and_warnings_are_bounded(tmp_path, caplog):
    cfg = Config(tmp_path)
    with patch("hermes_fry_cards.config.time.monotonic", return_value=10):
        for n in range(8):
            _write(tmp_path, f"streaming: [fixture-secret-{n}")
            assert cfg.enabled is True
    assert len(caplog.records) == 1
    assert "fixture-secret" not in caplog.text
    with patch("hermes_fry_cards.config.time.monotonic", return_value=71):
        _write(tmp_path, "[]")
        assert cfg.enabled is True
    assert len(caplog.records) == 2
    _write(tmp_path, {"streaming": {"enabled": False}})
    assert cfg.enabled is False


@pytest.mark.parametrize("error", [OSError("fixture-secret"), UnicodeError("fixture-secret")])
def test_file_read_errors_keep_valid_config(tmp_path, caplog, error):
    cfg = Config(tmp_path)
    _write(tmp_path, {"streaming": {"enabled": False}})
    assert cfg.enabled is False
    _write(tmp_path, {"streaming": {"enabled": True}})
    with patch.object(Path, "read_text", side_effect=error):
        assert cfg.enabled is False
    assert "fixture-secret" not in caplog.text
    _write(tmp_path, {"streaming": {"enabled": True}})
    assert cfg.enabled is True


@pytest.mark.parametrize("operation", ["stat", "read_text"])
def test_concurrent_io_failures_keep_last_good_and_throttle_warnings(tmp_path, caplog, operation):
    cfg = Config(tmp_path)
    _write(tmp_path, {"streaming": {"enabled": False}})
    assert cfg.enabled is False
    _write(tmp_path, {"streaming": {"enabled": True}})
    with patch.object(Path, operation, side_effect=OSError("fixture-private-marker")):
        with (
            patch("hermes_fry_cards.config.time.monotonic", return_value=10),
            ThreadPoolExecutor(max_workers=8) as pool,
        ):
            assert list(pool.map(lambda _: cfg.enabled, range(32))) == [False] * 32
        assert len(caplog.records) == 1
        with patch("hermes_fry_cards.config.time.monotonic", return_value=71):
            assert cfg.enabled is False
        assert len(caplog.records) == 2
    assert "fixture-private-marker" not in caplog.text
    # Transient I/O failures retry successfully with unchanged file metadata.
    assert cfg.enabled is True


def test_metadata_cache_parses_once_across_hot_properties(tmp_path):
    _write(tmp_path, {"streaming": {"enabled": False}, "display": {"show_reasoning": False}})
    cfg = Config(tmp_path)
    with patch("hermes_fry_cards.config.yaml.safe_load", wraps=yaml.safe_load) as parse:
        for _ in range(5):
            assert cfg.enabled is False
            assert cfg.show_reasoning is False
            assert cfg.width_mode == "default"
            assert cfg.content_lang == "zh"
        assert parse.call_count == 1


@pytest.mark.parametrize("changed_during_parse", [False, True])
def test_concurrent_reload_commits_matching_metadata_and_content(tmp_path, changed_during_parse):
    cfg = Config(tmp_path)
    _write(tmp_path, {"streaming": {"width_mode": "default"}})
    assert cfg.width_mode == "default"
    _write(tmp_path, {"streaming": {"width_mode": "compact"}})
    parse_started = Event()
    release_parse = Event()
    second_started = Event()
    second_done = Event()
    safe_load = yaml.safe_load

    def slow_parse(text):
        raw = safe_load(text)
        if raw["streaming"]["width_mode"] == "compact":
            parse_started.set()
            assert release_parse.wait(5), "first parser was not released"
        return raw

    def second_read():
        second_started.set()
        try:
            return cfg.width_mode
        finally:
            second_done.set()

    with patch("hermes_fry_cards.config.yaml.safe_load", side_effect=slow_parse) as parse:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(lambda: cfg.width_mode)
            try:
                assert parse_started.wait(5), "first parser did not start"
                if changed_during_parse:
                    _write(tmp_path, {"streaming": {"width_mode": "fill"}})
                second = pool.submit(second_read)
                assert second_started.wait(5), "second reader did not start"
                # An unlocked reader can finish while the old parse is paused.
                # A serialized reader waits here until the first commit completes.
                second_done.wait(0.2)
            finally:
                release_parse.set()
            assert first.result(timeout=5) == "compact"
            expected = "fill" if changed_during_parse else "compact"
            assert second.result(timeout=5) == expected
        for _ in range(5):
            assert cfg.width_mode == expected
        assert parse.call_count == (2 if changed_during_parse else 1)


def test_metadata_cache_detects_mtime_and_size_changes(tmp_path):
    path = _write(tmp_path, {"streaming": {"width_mode": "fill"}})
    cfg = Config(tmp_path)
    with patch("hermes_fry_cards.config.yaml.safe_load", wraps=yaml.safe_load) as parse:
        assert cfg.width_mode == "fill"
        initial = path.stat()
        _write(tmp_path, {"streaming": {"width_mode": "nope"}})
        assert path.stat().st_size == initial.st_size
        assert cfg.width_mode == "default"
        assert parse.call_count == 2
        current_mtime = path.stat().st_mtime_ns
        _write(tmp_path, {"streaming": {"width_mode": "compact"}})
        os.utime(path, ns=(current_mtime, current_mtime))
        assert cfg.width_mode == "compact"
        assert parse.call_count == 3
        assert cfg.width_mode == "compact"
        assert parse.call_count == 3


def test_snapshot_is_deep_and_display_stays_live(tmp_path):
    cfg = Config(tmp_path)
    _write(tmp_path, {
        "streaming": {"footer": {"fields": [["model"]]}, "process_expanded": False},
        "display": {"show_reasoning": False, "show_tool_use": False},
    })
    snapshot = cfg.card_snapshot()
    cfg._streaming_sec()["footer"]["fields"][0].append("status")
    assert snapshot.footer_fields == [["model"]]
    snapshot.footer_fields[0].append("elapsed")
    assert cfg.footer_fields == [["model", "status"]]
    _write(tmp_path, {
        "streaming": {"process_expanded": True},
        "display": {"show_reasoning": False, "show_tool_use": True,
                    "platforms": {"feishu": {"show_reasoning": True, "show_tool_use": False}}},
    })
    assert snapshot.process_expanded is False
    assert cfg.process_expanded is True
    assert snapshot.show_reasoning is cfg.show_reasoning is True
    assert snapshot.show_tool_use is cfg.show_tool_use is False
    assert snapshot._home == tmp_path


def test_snapshot_owns_independent_reload_lock(tmp_path):
    _write(tmp_path, {"streaming": {"width_mode": "compact"}, "display": {"show_reasoning": False}})
    cfg = Config(tmp_path)
    snapshot = cfg.card_snapshot()
    assert snapshot._reload_lock is not cfg._reload_lock
    _write(tmp_path, {"streaming": {"width_mode": "fill"}, "display": {"show_reasoning": True}})
    # Holding the source lock in this thread must not block the snapshot's reader.
    with ThreadPoolExecutor(max_workers=1) as pool, cfg._reload_lock:
        refreshed = pool.submit(lambda: (snapshot.width_mode, snapshot.show_reasoning))
        assert refreshed.result(timeout=5) == ("compact", True)
    assert cfg.width_mode == "fill"


def test_empty_snapshot_stays_frozen(tmp_path):
    cfg = Config(tmp_path)
    _write(tmp_path, {})
    snapshot = cfg.card_snapshot()
    _write(tmp_path, {"streaming": {"enabled": False}})
    assert snapshot.enabled is True
    assert cfg.enabled is False


def test_credentials_keep_cold_file_cache_and_snapshot_inherits_it(tmp_path):
    _write(tmp_path, {"feishu": {"app_id": "fixture-id-1", "app_secret": "fixture-secret-1"},
                      "streaming": {"enabled": True}})
    cfg = Config(tmp_path)
    with patch("hermes_fry_cards.config._get_secret", return_value=""):
        with patch("hermes_fry_cards.config.yaml.safe_load", wraps=yaml.safe_load) as parse:
            assert cfg.feishu_app_id == "fixture-id-1"
            assert cfg.feishu_app_secret == "fixture-secret-1"
            assert cfg.feishu_base_url == "https://open.feishu.cn"
            assert parse.call_count == 1
        _write(tmp_path, {"feishu": {"app_id": "fixture-id-2", "app_secret": "fixture-secret-2"},
                          "streaming": {"enabled": False}})
        assert cfg.enabled is False
        snapshot = cfg.card_snapshot()
        with patch.object(Path, "read_text", side_effect=AssertionError("cold file reread")):
            assert cfg.feishu_app_id == snapshot.feishu_app_id == "fixture-id-1"
            assert cfg.feishu_app_secret == snapshot.feishu_app_secret == "fixture-secret-1"


@pytest.mark.parametrize("value", [None, [], {}, "nope", 0, -1, float("nan"), float("inf"), -float("inf"), 86401])
def test_invalid_ttl_defaults(tmp_path, value):
    _write(tmp_path, {"streaming": {"card_ttl_sec": value}})
    assert Config(tmp_path).card_duration_sec == 600


@pytest.mark.parametrize(("value", "expected"), [(1, 1), (86400, 86400), ("900", 900), (10.9, 10), (0.5, 1)])
def test_valid_ttl_is_positive_and_bounded(tmp_path, value, expected):
    _write(tmp_path, {"streaming": {"card_ttl_sec": value}})
    assert Config(tmp_path).card_duration_sec == expected


@pytest.mark.parametrize("value", [None, [], "bad", 42])
def test_malformed_footer_label_is_guarded(tmp_path, value):
    _write(tmp_path, {"streaming": {"footer": value}})
    assert Config(tmp_path).footer_show_label is False


@pytest.mark.parametrize("value", [None, [], {}, "red", "#123", "123456", "#zzzzzz", " #123456", "#123456\n"])
def test_body_colors_validate_six_hex_digits(tmp_path, value):
    _write(tmp_path, {"streaming": {"body": {"light_color": value, "dark_color": value}}})
    cfg = Config(tmp_path)
    assert cfg.body_light_color == "#F5F6F7"
    assert cfg.body_dark_color == "#222222"


@pytest.mark.parametrize("value", HEADER_TEMPLATES)
def test_header_template_enum(tmp_path, value):
    _write(tmp_path, {"streaming": {"header": {"template": value}}})
    assert Config(tmp_path).header_template == value


@pytest.mark.parametrize("value", [None, [], {}, "unknown", "Blue"])
def test_invalid_header_template_defaults(tmp_path, value):
    _write(tmp_path, {"streaming": {"header": {"template": value}}})
    assert Config(tmp_path).header_template == "default"


@pytest.fixture
def live_language(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "_CONTENT_LANG_CACHE", None)
    monkeypatch.setattr(i18n, "hermes_home", lambda: tmp_path)
    i18n._content_config.cache_clear()
    prepare_streaming_markdown.cache_clear()
    yield
    i18n._content_config.cache_clear()
    prepare_streaming_markdown.cache_clear()


def test_language_reloads_and_markdown_cache_keys_resolved_language(tmp_path, live_language):
    raw = "\n\n".join(["| A | B |\n|---|---|\n| 1 | 2 |"] * 6)
    _write(tmp_path, {"streaming": {"content_lang": "zh"}})
    config = i18n._content_config(tmp_path)
    # 传入同一 profile 的配置：内容语言由卡片快照决定，热重读仍走 Config 的安全路径
    first = prepare_streaming_markdown(raw, config=config)
    assert "紧凑字段列表" in first
    assert "已截断" in clamp_utf8("a" * 20000, preserve_tail=False)
    assert prepare_streaming_markdown(raw, config=config) == first
    _write(tmp_path, {"streaming": {"content_lang": "en"}})
    second = prepare_streaming_markdown(raw, config=config)
    assert "compact field lists" in second
    assert "紧凑字段列表" not in second
    assert "truncated" in clamp_utf8("a" * 20000, preserve_tail=False)
    assert prepare_streaming_markdown(raw, config=config) == second
    assert i18n._content_config(tmp_path) is config
    assert prepare_streaming_markdown.cache_info().misses == 2
    assert prepare_streaming_markdown.cache_info().hits == 2
    _write(tmp_path, "streaming: [")
    assert prepare_streaming_markdown(raw, config=config) == second
    _write(tmp_path, {"streaming": {"content_lang": "zh"}})
    assert prepare_streaming_markdown(raw, config=config) == first
    assert prepare_streaming_markdown.cache_info().misses == 2
    prepare_streaming_markdown.cache_clear()
    assert prepare_streaming_markdown.cache_info().currsize == 0


def test_language_config_is_reused_per_home(tmp_path, monkeypatch, live_language):
    other = tmp_path / "other-profile"
    other.mkdir()
    _write(tmp_path, {"streaming": {"content_lang": "zh"}})
    _write(other, {"streaming": {"content_lang": "en"}})
    with patch("hermes_fry_cards.config.yaml.safe_load", wraps=yaml.safe_load) as parse:
        assert i18n._content_lang() == "zh"
        assert i18n._content_lang() == "zh"
        monkeypatch.setattr(i18n, "hermes_home", lambda: other)
        assert i18n._content_lang() == "en"
        monkeypatch.setattr(i18n, "hermes_home", lambda: tmp_path)
        assert i18n._content_lang() == "zh"
        assert parse.call_count == 2
    assert i18n._content_t("fixture-unregistered") == "fixture-unregistered"
