"""Studio 可视化工作坊测试 — 校验 / 拒写 / 备份 / 白名单 / 原子写 / HTTP 冒烟 / 预览."""

from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
import threading
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml

import hermes_fry_cards.studio.server as srv
from hermes_fry_cards.cardkit.builder import body_content_elements


class TestHeaderThemePreview:
    def test_each_template_has_visible_css(self) -> None:
        css = (srv._WEB_ROOT / "css/style.css").read_text(encoding="utf-8")
        for template in srv.HEADER_TEMPLATES:
            assert re.search(r"\.card-header\.t-" + template + r"\s*\{[^}]*background:", css)
        default = re.search(r"\.card-header\.t-default\s*\{([^}]+)\}", css)
        assert default and "color: inherit" in default.group(1)
        assert "background: transparent" in default.group(1)

    def test_default_template_in_rendered_dom(self) -> None:
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is required for JavaScript preview checks")
        assert node is not None
        script = r"""
const assert = require('node:assert/strict');
global.window = {};
global.document = {createElement() { return {
  children: [], appendChild(child) { this.children.push(child); }
}; }};
require(process.argv[1]);
for (const template of JSON.parse(process.argv[2])) {
  const header = {title: {content: 'title'}};
  if (template !== null) header.template = template;
  const card = window.FryPreview.renderCard({header, body: {elements: []}});
  assert.equal(card.children[0].className, 'card-header t-' + (template || 'default'));
  assert.equal(card.children[0].textContent, 'title');
}
"""
        subprocess.run(
            [node, "-e", script, str(srv._WEB_ROOT / "js/preview.js"),
             json.dumps([None, *srv.HEADER_TEMPLATES])],
            check=True, capture_output=True, text=True,
        )


_INITIAL_YAML = (
    "streaming:\n"
    "  enabled: true\n"
    "  card_ttl_sec: 600\n"  # 未包含在保存请求中的受管键保持原值
    "agents:\n"
    "  keep: me\n"
)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(srv, "hermes_home", lambda: tmp_path)
    monkeypatch.setattr(srv, "_HOST_CONFIG_CACHE", None)
    (tmp_path / "config.yaml").write_text(_INITIAL_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def server(home: Path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.StudioHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _req(
    base: str,
    path: str,
    payload: Any = None,
    *,
    raw: bytes | None = None,
) -> tuple[int, bytes, Any]:
    data = raw if raw is not None else (json.dumps(payload).encode("utf-8") if payload is not None else None)
    headers = {"Content-Type": "application/json"} if payload is not None and raw is None else {}
    req = Request(base + path, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), resp.headers
    except HTTPError as e:
        return e.code, e.read(), e.headers


def _post_ok(base: str, path: str, payload: Any) -> dict[str, Any]:
    code, body, _ = _req(base, path, payload)
    assert code == 200, body
    return json.loads(body)


def _get_json(base: str, path: str) -> dict[str, Any]:
    code, body, _ = _req(base, path)
    assert code == 200, body
    return json.loads(body)


# ---------------------------------------------------------------------------
# 纯函数：校验
# ---------------------------------------------------------------------------


class TestValidation:
    def test_unknown_top_level_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知顶层"):
            srv.validate_payload({"hacker": 1})

    def test_empty_payload_rejected(self) -> None:
        with pytest.raises(ValueError, match="至少"):
            srv.validate_payload({})

    def test_section_not_dict_rejected(self) -> None:
        with pytest.raises(ValueError, match="必须是对象"):
            srv.validate_payload({"streaming": "yes"})

    def test_unknown_nested_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知字段"):
            srv.validate_payload({"streaming": {"nonsense": True}})

    @pytest.mark.parametrize(
        ("bad", "match"),
        [
            ({"streaming": {"width_mode": "wide"}}, "width_mode"),
            ({"streaming": {"enabled": "yes"}}, "enabled"),
            ({"streaming": {"content_lang": "fr"}}, "content_lang"),
            ({"streaming": {"header": {"min_duration": -1}}}, "min_duration"),
            ({"streaming": {"footer": {"fields": [["status", "bogus"]]}}}, "字段"),
            ({"streaming": {"footer": {"fields": [["status", "status"]]}}}, "重复"),
            ({"streaming": {"footer": {"fields": "status"}}}, "二维数组"),
            ({"streaming": {"chat_types": "dm"}}, "数组"),
            ({"display": {"max_reasoning_panels": 0}}, "max_reasoning_panels"),
            ({"display": {"max_reasoning_panels": True}}, "max_reasoning_panels"),
            ({"display": {"context_display_mode": "huge"}}, "context_display_mode"),
            ({"display": {"unified_panel_min_duration": 601}}, "unified_panel_min_duration"),
            ({"display": {"panel_fields": "model"}}, "数组"),
            ({"display": {"panel_fields": ["model", "bogus"]}}, "字段"),
            ({"display": {"panel_fields": ["speed", "speed"]}}, "重复"),
        ],
    )
    def test_invalid_values_rejected(self, bad: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            srv.validate_payload(bad)

    def test_valid_normalizes(self) -> None:
        out = srv.validate_payload(
            {
                "streaming": {
                    "width_mode": "fill",
                    "chat_types": ["dm", "dm"],
                    "footer": {"fields": [["status", "model"]]},
                },
                "display": {"max_reasoning_panels": 7},
            }
        )
        assert out["streaming"]["width_mode"] == "fill"
        assert out["streaming"]["chat_types"] == ["dm"]  # 去重
        assert out["display"]["max_reasoning_panels"] == 7

    def test_footer_fields_accept_speed_cache(self) -> None:
        out = srv.validate_payload(
            {"streaming": {"footer": {"fields": [["status", "elapsed", "speed", "cache"]]}}}
        )
        assert out["streaming"]["footer"]["fields"] == [["status", "elapsed", "speed", "cache"]]

    def test_panel_fields_validate_and_merge(self) -> None:
        out = srv.validate_payload({"display": {"panel_fields": ["cache", "speed", "elapsed"]}})
        assert out["display"]["panel_fields"] == ["cache", "speed", "elapsed"]
        merged, _ = srv.merge_managed({}, out)
        assert merged["display"]["platforms"]["feishu"]["panel_fields"] == ["cache", "speed", "elapsed"]
        # 空列表合法（运行时回落默认布局）
        out = srv.validate_payload({"display": {"panel_fields": []}})
        assert out["display"]["panel_fields"] == []

    def test_panel_text_size_validate_and_merge(self) -> None:
        out = srv.validate_payload({"display": {"panel_text_size": "heading"}})
        assert out["display"]["panel_text_size"] == "heading"
        merged, _ = srv.merge_managed({}, out)
        assert merged["display"]["platforms"]["feishu"]["panel_text_size"] == "heading"
        with pytest.raises(ValueError, match="panel_text_size"):
            srv.validate_payload({"display": {"panel_text_size": "huge"}})

    def test_partial_sections_allowed(self) -> None:
        out = srv.validate_payload({"display": {"show_reasoning": False}})
        assert "streaming" not in out
        assert out["display"]["show_reasoning"] is False

    @pytest.mark.parametrize("size", ["normal_v2", "normal", "heading", "notation"])
    def test_textsize_accepts_supported_values(self, size: str) -> None:
        out = srv.validate_payload({"streaming": {"body": {"text_size": size}}})
        assert out["streaming"]["body"]["text_size"] == size

    def test_textsize_rejects_unknown_value(self) -> None:
        with pytest.raises(ValueError, match="text_size"):
            srv.validate_payload({"streaming": {"body": {"text_size": "huge"}}})

    def test_completion_notice_settings_validate_and_merge(self) -> None:
        payload = srv.validate_payload({"streaming": {
            "completion_notice": True,
            "completion_notice_text": "完成啦",
        }})
        merged, _ = srv.merge_managed({}, payload)
        assert merged["streaming"]["completion_notice"] is True
        assert merged["streaming"]["completion_notice_text"] == "完成啦"
        with pytest.raises(ValueError, match="completion_notice_text"):
            srv.validate_payload({"streaming": {"completion_notice_text": " "}})

    def test_completion_notice_state_roundtrip(self, server: str, home: Path) -> None:
        _post_ok(server, "/api/config", {"streaming": {
            "completion_notice": True,
            "completion_notice_text": "答复完成",
        }})
        state = _get_json(server, "/api/state")
        assert state["streaming"]["completion_notice"] is True
        assert state["streaming"]["completion_notice_text"] == "答复完成"
        cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        assert cfg["streaming"]["completion_notice"] is True
        assert cfg["streaming"]["completion_notice_text"] == "答复完成"

    def test_preview_uses_heading_text_size(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {
            "scenario": "short",
            "overrides": {"streaming": {"body": {"text_size": "heading"}}},
        })
        assert any(
            element.get("text_size") == "heading"
            for element in body_content_elements(data["card"])
        )

    def test_studio_ui_exposes_notice_and_heading(self) -> None:
        html = (srv._WEB_ROOT / "index.html").read_text(encoding="utf-8")
        assert 'id="f-completion-notice"' in html
        assert 'id="f-completion-notice-text"' in html
        assert 'value="heading"' in html
        js = (srv._WEB_ROOT / "js" / "app.js").read_text(encoding="utf-8")
        assert '"f-completion-notice"' in js
        assert '"f-completion-notice-text"' in js
        assert 'completion_notice_text:' in js

    def test_studio_ui_exposes_panel_text_size(self) -> None:
        """统一面板 header 字体控件在 HTML / 读写两端都接通（display.panel_text_size）."""
        html = (srv._WEB_ROOT / "index.html").read_text(encoding="utf-8")
        assert 'id="f-panel-text-size"' in html
        js = (srv._WEB_ROOT / "js" / "app.js").read_text(encoding="utf-8")
        assert 'setVal("f-panel-text-size", d.panel_text_size)' in js
        assert 'panel_text_size: $("#f-panel-text-size").value' in js
        i18n = (srv._WEB_ROOT / "js" / "i18n.js").read_text(encoding="utf-8")
        assert '"lbl.panel-text-size"' in i18n

    def test_studio_ui_exposes_flush_interval_ms(self) -> None:
        """流式刷新节流输入控件在 HTML / 读写两端都接通（streaming.flush_interval_ms）."""
        html = (srv._WEB_ROOT / "index.html").read_text(encoding="utf-8")
        assert 'id="f-flush-interval-ms"' in html
        js = (srv._WEB_ROOT / "js" / "app.js").read_text(encoding="utf-8")
        assert 'setVal("f-flush-interval-ms", s.flush_interval_ms)' in js
        assert 'flush_interval_ms: int("#f-flush-interval-ms")' in js
        i18n = (srv._WEB_ROOT / "js" / "i18n.js").read_text(encoding="utf-8")
        assert '"lbl.flush-interval"' in i18n


# ---------------------------------------------------------------------------
# 纯函数：白名单合并 / 备份 / 原子写
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 纯函数：白名单合并 / 备份 / 原子写
# ---------------------------------------------------------------------------


class TestMergeAndWrite:
    def test_user_keys_survive(self) -> None:
        base = yaml.safe_load(_INITIAL_YAML)
        merged, changed = srv.merge_managed(base, srv.validate_payload({"streaming": {"width_mode": "compact"}}))
        assert merged["agents"] == {"keep": "me"}
        assert merged["streaming"]["card_ttl_sec"] == 600  # 非受管键存活
        assert merged["streaming"]["width_mode"] == "compact"
        assert "streaming.width_mode" in changed

    def test_display_written_to_platform_override(self) -> None:
        base = {"display": {"show_reasoning": True, "platforms": {"lark": {"x": 1}}}}
        merged, changed = srv.merge_managed(base, srv.validate_payload({"display": {"show_reasoning": False}}))
        assert merged["display"]["show_reasoning"] is True  # 根键不动
        assert merged["display"]["platforms"]["feishu"]["show_reasoning"] is False
        assert merged["display"]["platforms"]["lark"] == {"x": 1}  # 同级平台存活
        assert changed == ["display.platforms.feishu.show_reasoning"]

    def test_noop_save_reports_empty_changes(self) -> None:
        base = {"streaming": {"width_mode": "fill"}}
        _, changed = srv.merge_managed(base, srv.validate_payload({"streaming": {"width_mode": "fill"}}))
        assert changed == []

    def test_structural_shape_refused(self) -> None:
        with pytest.raises(srv.ConfigReadError):
            srv.merge_managed({"streaming": "broken"}, {"streaming": {"width_mode": "fill"}})

    def test_read_unparsable_refused(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text("streaming: [unclosed", encoding="utf-8")
        with pytest.raises(srv.ConfigReadError):
            srv.read_full_config(p)

    def test_read_missing_returns_empty(self, tmp_path: Path) -> None:
        assert srv.read_full_config(tmp_path / "nope.yaml") == {}

    def test_write_atomic_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        srv.write_atomic(p, {"streaming": {"enabled": True, "chat_types": None}})
        assert not (tmp_path / "config.yaml.tmp").exists()
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert data["streaming"]["enabled"] is True
        assert data["streaming"]["chat_types"] is None

    def test_backup_rotation(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(_INITIAL_YAML, encoding="utf-8")
        for _ in range(25):
            srv.backup_config(p)
        backups = list((tmp_path / "backups" / "fry_studio").glob("config.yaml.bak_*"))
        assert len(backups) == srv._BACKUP_KEEP


# ---------------------------------------------------------------------------
# HTTP：静态 / 门禁 / 状态
# ---------------------------------------------------------------------------


class TestHttpBasics:
    def test_index_served(self, server: str) -> None:
        code, body, headers = _req(server, "/")
        assert code == 200
        assert "fry-cards Studio" in body.decode("utf-8")
        assert "text/html" in headers["Content-Type"]

    def test_js_served(self, server: str) -> None:
        code, body, headers = _req(server, "/js/app.js")
        assert code == 200
        assert "text/javascript" in headers["Content-Type"]
        assert len(body) > 500

    @pytest.mark.parametrize(
        "path",
        ["/", "/index.html", "/css/style.css", "/js/preview.js", "/js/app.js"],
    )
    def test_all_web_assets_served(self, server: str, path: str) -> None:
        """所有前端资源必须可取 — preview.js 曾静默缺失导致 app.js 整体挂掉."""
        code, body, _ = _req(server, path)
        assert code == 200, path
        assert len(body) > 500, path

    def test_path_traversal_blocked(self, server: str) -> None:
        host, port = server.removeprefix("http://").split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        conn.request("GET", "/css/../../pyproject.toml")
        resp = conn.getresponse()
        assert resp.status == 404
        resp.read()
        conn.close()

    def test_foreign_host_forbidden(self, server: str) -> None:
        host, port = server.removeprefix("http://").split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        conn.putrequest("GET", "/api/state", skip_host=True)
        conn.putheader("Host", "evil.example.com")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()
        conn.close()

    def test_no_cors_header(self, server: str) -> None:
        _, _, headers = _req(server, "/api/state")
        assert "Access-Control-Allow-Origin" not in headers

    def test_unknown_route_404(self, server: str) -> None:
        code, _, _ = _req(server, "/api/nope")
        assert code == 404

    def test_oversized_body_413(self, server: str) -> None:
        raw = b'{"x":"' + b"a" * (srv._MAX_BODY_BYTES + 100) + b'"}'
        code, _, _ = _req(server, "/api/config", raw=raw)
        assert code == 413

    def test_malformed_json_400(self, server: str) -> None:
        code, body, _ = _req(server, "/api/config", raw=b"{not json")
        assert code == 400
        assert json.loads(body)["ok"] is False

    def test_state_shape_and_no_secrets(self, server: str) -> None:
        data = _get_json(server, "/api/state")
        assert data["ok"] is True
        assert set(data["streaming"]) >= {"enabled", "width_mode", "header", "footer", "body"}
        assert set(data["display"]) >= {"show_reasoning", "max_reasoning_panels"}
        st = data["status"]
        assert set(st["verify"]) == {"gateway", "cron", "clarify"}
        assert isinstance(st["credentials"], bool)
        # 响应中绝不出现凭证键名与值
        raw = json.dumps(data, ensure_ascii=False)
        assert "app_secret" not in raw
        assert "app_id" not in raw

    def test_state_reflects_user_yaml(self, server: str) -> None:
        data = _get_json(server, "/api/state")
        assert data["streaming"]["enabled"] is True
        assert data["streaming"]["chat_types"] is None  # 未配置 = 全部


# ---------------------------------------------------------------------------
# HTTP：写回端点
# ---------------------------------------------------------------------------


class TestConfigEndpoint:
    def test_valid_save_writes_and_backups(self, server: str, home: Path) -> None:
        data = _post_ok(
            server,
            "/api/config",
            {"streaming": {"width_mode": "fill", "header": {"enabled": False, "min_duration": 2.5}}},
        )
        assert "streaming.width_mode" in data["changed"]
        conf = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        assert conf["streaming"]["width_mode"] == "fill"
        assert conf["streaming"]["header"] == {"enabled": False, "min_duration": 2.5}
        assert conf["agents"] == {"keep": "me"}
        assert conf["streaming"]["card_ttl_sec"] == 600
        backups = list((home / "backups" / "fry_studio").glob("config.yaml.bak_*"))
        assert len(backups) == 1
        assert not (home / "config.yaml.tmp").exists()

    def test_invalid_value_leaves_file_untouched(self, server: str, home: Path) -> None:
        before = (home / "config.yaml").read_bytes()
        code, body, _ = _req(server, "/api/config", {"streaming": {"width_mode": "wide"}})
        assert code == 400
        assert "width_mode" in json.loads(body)["error"]
        assert (home / "config.yaml").read_bytes() == before
        assert not list((home / "backups" / "fry_studio").glob("config.yaml.bak_*"))

    def test_unparsable_config_refused_409(self, server: str, home: Path) -> None:
        broken = "streaming: [unclosed"
        (home / "config.yaml").write_text(broken, encoding="utf-8")
        code, body, _ = _req(server, "/api/config", {"streaming": {"width_mode": "fill"}})
        assert code == 409
        assert json.loads(body)["ok"] is False
        assert (home / "config.yaml").read_text(encoding="utf-8") == broken  # 一字未动

    def test_display_override_written(self, server: str, home: Path) -> None:
        _post_ok(server, "/api/config", {"display": {"show_reasoning": False, "context_display_mode": "bar"}})
        conf = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        feishu = conf["display"]["platforms"]["feishu"]
        assert feishu["show_reasoning"] is False
        assert feishu["context_display_mode"] == "bar"

    def test_chat_types_null_roundtrip(self, server: str) -> None:
        _post_ok(server, "/api/config", {"streaming": {"chat_types": ["dm"]}})
        data = _get_json(server, "/api/state")
        assert data["streaming"]["chat_types"] == ["dm"]
        _post_ok(server, "/api/config", {"streaming": {"chat_types": None}})
        data = _get_json(server, "/api/state")
        assert data["streaming"]["chat_types"] is None

    def test_backup_capped_at_20(self, server: str, home: Path) -> None:
        for i in range(22):
            mode = "fill" if i % 2 == 0 else "default"
            _post_ok(server, "/api/config", {"streaming": {"width_mode": mode}})
        backups = list((home / "backups" / "fry_studio").glob("config.yaml.bak_*"))
        assert len(backups) == srv._BACKUP_KEEP


# ---------------------------------------------------------------------------
# HTTP：预览端点
# ---------------------------------------------------------------------------


class TestPreviewEndpoint:
    def test_completed_workflow(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {"scenario": "workflow", "state": "completed"})
        card = data["card"]
        assert card["schema"] == "2.0"
        assert set(card["header"]) == {"title"}
        raw = json.dumps(card, ensure_ascii=False)
        assert "结论" in raw

    def test_streaming_state(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {"scenario": "workflow", "state": "streaming"})
        card = data["card"]
        assert card["config"]["streaming_mode"] is True
        assert set(card["header"]) == {"title"}

    def test_tables_compaction_visible(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {"scenario": "tables", "state": "completed"})
        raw = json.dumps(data["card"], ensure_ascii=False)
        assert "Table 6 · Row 1" in raw

    def test_longtext_clamped(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {"scenario": "longtext", "state": "completed"})
        raw = json.dumps(data["card"], ensure_ascii=False)
        assert "省略" in raw or "omitted" in raw

    def test_error_outcome_status_title(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {"scenario": "short", "state": "completed", "outcome": "error"})
        assert set(data["card"]["header"]) == {"title"}
        assert "Error" in data["card"]["header"]["title"]["content"]

    def test_overrides_not_persisted(self, server: str) -> None:
        data = _post_ok(
            server,
            "/api/preview",
            {"scenario": "short", "state": "completed", "overrides": {"streaming": {"header": {"enabled": False}}}},
        )
        assert "header" not in data["card"]  # override 生效
        state = _get_json(server, "/api/state")
        assert state["streaming"]["header"]["enabled"] is True  # 未落盘

    @pytest.mark.parametrize(
        "payload",
        [
            {"scenario": "nope"},
            {"scenario": "short", "state": "weird"},
            {"scenario": "short", "state": "completed", "outcome": "explode"},
            {"scenario": "short", "bogus_field": 1},
            {"scenario": "short", "overrides": {"streaming": {"width_mode": "wide"}}},
        ],
    )
    def test_invalid_preview_400(self, server: str, payload: dict) -> None:
        code, body, _ = _req(server, "/api/preview", payload)
        assert code == 400
        assert json.loads(body)["ok"] is False


# ---------------------------------------------------------------------------
# HTTP：重启端点（hermes CLI 缺失时如实回报）
# ---------------------------------------------------------------------------


class TestRestartEndpoint:
    def test_missing_cli_reported(self, server: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(srv.shutil, "which", lambda _name: None)
        data = _post_ok(server, "/api/restart", {})
        assert data["ok"] is False
        assert "hermes" in data["error"]


# ---------------------------------------------------------------------------
# HTTP：模型别名（时段人设格式兼容 claw-fry-cards）
# ---------------------------------------------------------------------------


class TestAliasesEndpoint:
    def test_get_missing_file_returns_empty(self, server: str) -> None:
        data = _get_json(server, "/api/aliases")
        assert data == {"ok": True, "entries": []}

    def test_roundtrip_string_and_time_entries(self, server: str, home: Path) -> None:
        # 预置旧别名文件 → 保存后备份的应是原件（首写无文件则无可备份）
        (home / "model_aliases.json").write_text('{"old": "旧名"}', encoding="utf-8")
        entries = [
            {"key": "mimo", "value": "小虾米"},
            {
                "key": "deepseek",
                "value": {
                    "name": "梁文谷⚡️",
                    "timeAliases": [
                        {"days": [1, 2, 3, 4, 5], "start": "09:00", "end": "12:00", "name": "梁文锋⚡️"},
                        {"days": "1-5,0", "start": "14:00", "end": "18:00", "name": "梁文锋⚡️"},
                    ],
                },
            },
        ]
        data = _post_ok(server, "/api/aliases", {"entries": entries})
        assert data["count"] == 2
        assert data["keys"] == ["mimo", "deepseek"]
        raw = json.loads((home / "model_aliases.json").read_text(encoding="utf-8"))
        assert list(raw) == ["mimo", "deepseek"]  # 插入序保留
        assert raw["deepseek"]["name"] == "梁文谷⚡️"
        assert raw["deepseek"]["timeAliases"][1]["days"] == "1-5,0"  # legacy 形态原样保留
        backups = sorted((home / "backups" / "fry_studio").glob("model_aliases.json.bak_*"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text(encoding="utf-8")) == {"old": "旧名"}  # 备份 = 写前原件
        back = _get_json(server, "/api/aliases")
        assert back["entries"] == entries

    def test_clear_all_entries(self, server: str, home: Path) -> None:
        _post_ok(server, "/api/aliases", {"entries": [{"key": "mimo", "value": "小虾米"}]})
        data = _post_ok(server, "/api/aliases", {"entries": []})
        assert data["count"] == 0
        assert json.loads((home / "model_aliases.json").read_text(encoding="utf-8")) == {}

    @pytest.mark.parametrize(
        "payload",
        [
            {"entries": [{"key": "a", "value": "x"}, {"key": "A", "value": "y"}]},  # 大小写重复
            {"entries": [{"key": "", "value": "x"}]},  # 空键
            {"entries": [{"key": " k", "value": "x"}]},  # 首尾空白
            {"entries": [{"key": "k", "value": 123}]},  # 值类型
            {"entries": [{"key": "k"}]},  # 缺 value
            {"entries": [{"k": "k", "value": "x"}]},  # 键名错
            {"entries": [{"key": "k", "value": {"name": "n", "bogus": 1}}]},  # 对象未知字段
            {"entries": [{"key": "k", "value": {"timeAliases": [{"days": [1]}]}}]},  # 规则缺 name
            {"entries": [{"key": "k", "value": {"timeAliases": [{"name": "x", "start": "9:00"}]}}]},  # 非 HH:MM
            {"entries": [{"key": "k", "value": {"timeAliases": [{"name": "x", "end": "24:00"}]}}]},  # 超界
            {"entries": [{"key": "k", "value": {"timeAliases": [{"name": "x", "days": [7]}]}}]},  # days 越界
            {"entries": [{"key": "k", "value": {"timeAliases": [{"name": "x", "days": "8-9"}]}}]},  # legacy days 越界
            {"entries": "oops"},
            {"entries": [], "extra": 1},
        ],
    )
    def test_invalid_entries_rejected_untouched(self, server: str, home: Path, payload: dict) -> None:
        code, body, _ = _req(server, "/api/aliases", payload)
        assert code == 400, body
        assert json.loads(body)["ok"] is False
        assert not (home / "model_aliases.json").exists()  # 一字未写

    def test_unparsable_aliases_file_refused(self, server: str, home: Path) -> None:
        path = home / "model_aliases.json"
        path.write_text("{broken", encoding="utf-8")
        gcode, _gbody, _ = _req(server, "/api/aliases")
        assert gcode == 409
        pcode, pbody, _ = _req(server, "/api/aliases", {"entries": [{"key": "k", "value": "v"}]})
        assert pcode == 409
        assert json.loads(pbody)["ok"] is False
        assert path.read_text(encoding="utf-8") == "{broken"

    def test_enabled_switch_in_state_and_config_roundtrip(self, server: str, home: Path) -> None:
        state = _get_json(server, "/api/state")
        assert state["display"]["model_aliases_enabled"] is True  # 缺省开
        _post_ok(server, "/api/config", {"display": {"model_aliases_enabled": False}})
        state = _get_json(server, "/api/state")
        assert state["display"]["model_aliases_enabled"] is False
        conf = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        assert conf["display"]["platforms"]["feishu"]["model_aliases_enabled"] is False
        _post_ok(server, "/api/config", {"display": {"model_aliases_enabled": True}})
        assert _get_json(server, "/api/state")["display"]["model_aliases_enabled"] is True


# ---------------------------------------------------------------------------
# HTTP：群聊安全边界（gateway.group_security_boundary）
# ---------------------------------------------------------------------------


class TestGroupSecurityBoundary:
    def test_state_default(self, server: str) -> None:
        state = _get_json(server, "/api/state")
        gsb = state["gateway"]["group_security_boundary"]
        assert gsb == {
            "enabled": False,
            "allow_chats": [],
            "non_admin_allowed_toolsets": [],
            "non_admin_disabled_toolsets": [],
        }

    def test_enable_and_roundtrip_with_dedupe(self, server: str, home: Path) -> None:
        payload = {
            "gateway": {
                "group_security_boundary": {"enabled": True, "allow_chats": ["oc_dev1", "oc_dev1", "oc_team2"]}
            }
        }
        data = _post_ok(server, "/api/config", payload)
        assert "gateway.group_security_boundary.enabled" in data["changed"]
        gsb = _get_json(server, "/api/state")["gateway"]["group_security_boundary"]
        assert gsb["enabled"] is True
        assert gsb["allow_chats"] == ["oc_dev1", "oc_team2"]
        conf = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        assert conf["gateway"]["group_security_boundary"]["enabled"] is True

    def test_toolset_lists_roundtrip_and_reject_junk(self, server: str, home: Path) -> None:
        """#55 工具隔离名单：数组清洗入库；非数组 / 带空白的项被 409 拒绝."""
        payload = {
            "gateway": {
                "group_security_boundary": {
                    "non_admin_allowed_toolsets": ["web", "web", "clarify"],
                    "non_admin_disabled_toolsets": ["terminal"],
                }
            }
        }
        data = _post_ok(server, "/api/config", payload)
        assert "gateway.group_security_boundary.non_admin_allowed_toolsets" in data["changed"]
        gsb = _get_json(server, "/api/state")["gateway"]["group_security_boundary"]
        assert gsb["non_admin_allowed_toolsets"] == ["web", "clarify"]
        assert gsb["non_admin_disabled_toolsets"] == ["terminal"]

        bad = {
            "gateway": {
                "group_security_boundary": {"non_admin_allowed_toolsets": "terminal"}
            }
        }
        code, _body, _headers = _req(server, "/api/config", bad)
        assert code == 400

    def test_sibling_gateway_keys_and_custom_text_preserved(self, server: str, home: Path) -> None:
        # 预置 gateway 段：兄弟键 + 未纳管的自定义 text 必须在保存后存活
        (home / "config.yaml").write_text(
            "gateway:\n"
            "  other_gw_key: 42\n"
            "  group_security_boundary:\n"
            "    enabled: false\n"
            "    text: CUSTOM-BOUNDARY-TEXT\n",
            encoding="utf-8",
        )
        _post_ok(
            server,
            "/api/config",
            {"gateway": {"group_security_boundary": {"enabled": True, "allow_chats": ["oc_x"]}}},
        )
        conf = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        gw = conf["gateway"]
        assert gw["other_gw_key"] == 42  # 兄弟键存活
        assert gw["group_security_boundary"]["text"] == "CUSTOM-BOUNDARY-TEXT"  # 未纳管键存活
        assert gw["group_security_boundary"]["enabled"] is True
        assert gw["group_security_boundary"]["allow_chats"] == ["oc_x"]

    @pytest.mark.parametrize(
        "gsb",
        [
            {"allow_chats": "oc_x"},  # 非数组
            {"allow_chats": ["has space"]},  # 含空白
            {"allow_chats": [""]},  # 空串
            {"allow_chats": ["x" * 65]},  # 超长
            {"allow_chats": [123]},  # 非字符串
            {"bogus": True},  # 未知字段
            {"enabled": "yes"},  # 非布尔
        ],
    )
    def test_invalid_gsb_rejected_untouched(self, server: str, home: Path, gsb: dict) -> None:
        before = (home / "config.yaml").read_bytes()
        code, body, _ = _req(server, "/api/config", {"gateway": {"group_security_boundary": gsb}})
        assert code == 400, body
        assert (home / "config.yaml").read_bytes() == before

    def test_unknown_gateway_section_rejected(self, server: str) -> None:
        code, _, _ = _req(server, "/api/config", {"gateway": {"other_section": {}}})
        assert code == 400

    def test_gsb_shape_mismatch_refused_409(self, server: str, home: Path) -> None:
        (home / "config.yaml").write_text("gateway:\n  group_security_boundary: broken\n", encoding="utf-8")
        code, body, _ = _req(
            server, "/api/config", {"gateway": {"group_security_boundary": {"enabled": True}}}
        )
        assert code == 409
        assert json.loads(body)["ok"] is False


class TestVersionInStatus:
    def test_fry_version_present(self, server: str) -> None:
        state = _get_json(server, "/api/state")
        ver = state["status"]["fry_version"]
        assert isinstance(ver, str) and ver
        # 与 pyproject 单一真相源一致
        from importlib.metadata import version as md_version

        assert ver == md_version("hermes-fry-cards")

    def test_hermes_version_key_present(self, server: str) -> None:
        # hermes CLI 缺失时为 None（容器/真机有 CLI 时为首行版本字符串）
        state = _get_json(server, "/api/state")
        assert "hermes_version" in state["status"]
        assert state["status"]["hermes_version"] is None or isinstance(
            state["status"]["hermes_version"], str
        )


class TestWildcardBindDefaults:
    """默认绑 0.0.0.0（局域网可达）是产品决策 — 三处落点都要守住，别被改回 loopback."""

    def test_run_studio_server_defaults_to_wildcard(self) -> None:
        import inspect

        assert inspect.signature(srv.run_studio_server).parameters["host"].default == "0.0.0.0"

    def test_cli_studio_defaults_to_wildcard(self) -> None:
        import inspect

        from hermes_fry_cards.__main__ import _cmd_studio

        assert 'host = "0.0.0.0"' in inspect.getsource(_cmd_studio)

    def test_systemd_template_binds_wildcard(self) -> None:
        template = (
            Path(__file__).resolve().parents[1] / "systemd" / "hermes-fry-cards-studio.service"
        )
        assert "--host 0.0.0.0" in template.read_text(encoding="utf-8")

    def test_wildcard_url_falls_back_to_loopback(self) -> None:
        # 浏览器/打印不能用 0.0.0.0
        assert srv._display_host("0.0.0.0") == "127.0.0.1"
        assert srv._display_host("::") == "127.0.0.1"
        assert srv._display_host("192.168.31.5") == "192.168.31.5"


class TestStudioHotStyleConfig:
    def test_style_validation_and_normalization(self) -> None:
        payload = {"streaming": {
            "body": {"light_color": "#a1b2c3", "dark_color": "#010203"},
            "header": {"template": "turquoise"},
            "process_border": True,
            "card_ttl_sec": 1200,
        }}
        cleaned = srv.validate_payload(payload)["streaming"]
        assert cleaned["body"] == {"light_color": "#A1B2C3", "dark_color": "#010203"}
        assert cleaned["header"]["template"] == "turquoise"
        assert cleaned["process_border"] is True
        assert cleaned["card_ttl_sec"] == 1200

    @pytest.mark.parametrize("streaming", [
        {"body": {"light_color": "red"}},
        {"body": {"dark_color": "#fff"}},
        {"body": {"light_color": "#12345678"}},
        {"body": {"dark_color": "#12345G"}},
        {"body": {"light_color": "#123456\n"}},
        {"body": {"dark_color": True}},
        {"header": {"template": "rainbow"}},
        {"process_border": "false"},
        {"card_ttl_sec": 0},
        {"card_ttl_sec": -1},
        {"card_ttl_sec": 86401},
        {"card_ttl_sec": True},
        {"card_ttl_sec": "600"},
        {"card_ttl_sec": float("nan")},
        {"card_ttl_sec": float("inf")},
    ])
    def test_invalid_style_values_rejected(self, streaming: dict) -> None:
        with pytest.raises(ValueError):
            srv.validate_payload({"streaming": streaming})

    def test_state_defaults_preserve_appearance(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(srv, "_collect_status", lambda _home: {})
        streaming = srv.collect_state(home)["streaming"]
        assert streaming["body"] == {
            "text_size": "normal_v2", "light_color": "#F5F6F7", "dark_color": "#222222",
        }
        assert streaming["header"]["template"] == "default"
        assert streaming["process_border"] is False
        assert streaming["card_ttl_sec"] == 600
        assert streaming["footer"]["text_size"] == "notation"

    def test_style_save_roundtrip(self, server: str, home: Path) -> None:
        changes = {"streaming": {
            "body": {"light_color": "#a1b2c3", "dark_color": "#334455"},
            "header": {"template": "violet"}, "process_border": True, "card_ttl_sec": 1200,
        }}
        result = _post_ok(server, "/api/config", changes)
        assert set(result["changed"]) == {
            "streaming.body.light_color", "streaming.body.dark_color", "streaming.header.template",
            "streaming.process_border", "streaming.card_ttl_sec",
        }
        state = _get_json(server, "/api/state")["streaming"]
        assert state["body"]["light_color"] == "#A1B2C3"
        assert state["body"]["dark_color"] == "#334455"
        assert state["header"]["template"] == "violet"
        assert state["process_border"] is True
        assert state["card_ttl_sec"] == 1200
        disk = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        assert disk["agents"] == {"keep": "me"}
        assert disk["streaming"]["body"]["light_color"] == "#A1B2C3"

    @pytest.mark.parametrize("state", ["streaming", "completed"])
    def test_preview_uses_isolated_style_snapshot(
        self, home: Path, monkeypatch: pytest.MonkeyPatch, state: str,
    ) -> None:
        monkeypatch.setattr(srv, "_collect_status", lambda _home: {})
        before = (home / "config.yaml").read_bytes()
        card = srv.build_preview({"state": state, "overrides": {"streaming": {
            "body": {"light_color": "#010203", "dark_color": "#aabbcc"},
            "header": {"template": "purple"}, "process_border": True,
        }}}, home)["card"]
        assert card["config"]["style"]["color"]["cus-0"] == {
            "light_mode": "rgba(1,2,3,1)", "dark_mode": "rgba(170,187,204,1)",
        }
        assert card["header"]["template"] == "purple"
        if state == "streaming":
            frames = [e for e in body_content_elements(card) if e.get("tag") == "collapsible_panel"]
            assert frames
            assert all(e["border"]["color"] == "grey" for e in frames)
        assert (home / "config.yaml").read_bytes() == before
        default = srv.build_preview({"state": state}, home)["card"]
        assert "template" not in default["header"]
        assert default["config"]["style"]["color"]["cus-0"] == {
            "light_mode": "rgba(245,246,247,1)", "dark_mode": "rgba(34,34,34,1)",
        }
        if state == "streaming":
            frames = [e for e in body_content_elements(default) if e.get("tag") == "collapsible_panel"]
            assert all("border" not in e for e in frames)

    @pytest.mark.parametrize("template", srv.HEADER_TEMPLATES)
    def test_all_official_header_templates(self, template: str) -> None:
        result = srv.validate_payload({"streaming": {"header": {"template": template}}})
        assert result["streaming"]["header"]["template"] == template


class TestStudioStyleDOM:
    def test_style_controls_labels_help_and_template_options(self) -> None:
        class Document(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.elements: dict[str, dict] = {}
                self.templates: list[str] = []
                self.i18n_keys: set[str] = set()
                self.select = ""

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                values = dict(attrs)
                element_id = values.get("id")
                if element_id:
                    assert element_id not in self.elements
                    self.elements[element_id] = values
                i18n_key = values.get("data-i18n")
                if i18n_key:
                    self.i18n_keys.add(i18n_key)
                if tag == "select":
                    self.select = element_id or ""
                if tag == "option" and self.select == "f-header-template":
                    value = values.get("value")
                    assert isinstance(value, str)
                    self.templates.append(value)

            def handle_endtag(self, tag: str) -> None:
                if tag == "select":
                    self.select = ""

        document = Document()
        document.feed((srv._WEB_ROOT / "index.html").read_text(encoding="utf-8"))
        js = (srv._WEB_ROOT / "js/app.js").read_text(encoding="utf-8")
        i18n = (srv._WEB_ROOT / "js/i18n.js").read_text(encoding="utf-8")
        assert set(document.templates) == set(srv.HEADER_TEMPLATES)
        for element_id in (
            "f-body-light-color", "f-body-dark-color", "f-header-template", "f-process-border", "f-card-ttl-sec",
        ):
            attrs = document.elements[element_id]
            for described_by in attrs["aria-describedby"].split():
                assert described_by in document.elements
            assert f'"{element_id}"' in js  # fillForm
            assert f'"#{element_id}"' in js  # readForm
        assert document.elements["f-body-light-color"]["type"] == "color"
        assert document.elements["f-body-dark-color"]["type"] == "color"
        assert document.elements["f-process-border"]["type"] == "checkbox"
        assert document.elements["f-card-ttl-sec"]["max"] == "86400"
        assert document.elements["error-card-ttl"]["role"] == "alert"
        assert document.i18n_keys <= set(re.findall(r'"([\w.-]+)"\s*:', i18n)) | {"hint.warn"}
        assert "已有卡片保留创建时样式" in i18n
        assert "existing cards keep their initial style" in i18n

    def test_form_mapping_and_ttl_validation_in_node(self) -> None:
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is required for JavaScript form checks")
        assert node is not None
        script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
const app = fs.readFileSync(process.argv[1], "utf8");
const i18n = fs.readFileSync(process.argv[2], "utf8");
const controls = new Map();
function control(id) {
  if (!controls.has(id)) controls.set(id, {
    value: "", checked: false, options: [], hidden: false, textContent: "",
    attrs: {}, classList: { toggle() {}, contains() { return false; } },
    querySelectorAll() { return []; }, appendChild(el) { this.options.push(el); },
    setCustomValidity(value) { this.validity = value; },
    setAttribute(key, value) { this.attrs[key] = value; },
  });
  return controls.get(id);
}
const context = {
  window: {}, localStorage: { getItem() { return null; } }, location: { search: "", hash: "" },
  document: {
    getElementById: control, querySelector: sel => control(sel.replace(/^#/, "")),
    querySelectorAll() { return []; }, createElement() { return {}; },
    documentElement: { setAttribute() {} },
  },
  $: sel => control(sel.replace(/^#/, "")),
  __origChatTypes: null, __origFields: [], orderRows() {}, syncFoldBadges() {},
};
vm.createContext(context);
vm.runInContext(i18n, context);
context.I18N = context.window.FryI18n;
// Run the actual form functions; unrelated navigation and network handlers stay outside this harness.
for (const name of ["setVal", "setCheck", "fillForm", "num", "int", "readForm", "validateCardTTL"]) {
  const start = app.indexOf("  function " + name + "(");
  assert.ok(start >= 0, name);
  const end = app.indexOf("\n  }", start) + 4;
  vm.runInContext(app.slice(start, end), context);
}
const state = {
  streaming: {
    enabled: true, width_mode: "default", chat_types: null,
    header: { enabled: true, min_duration: 0, template: "violet" },
    footer: { enabled: false, show_label: false, text_size: "notation", fields: [] },
    body: { text_size: "normal_v2", light_color: "#a1b2c3", dark_color: "#334455" },
    process_border: true, card_ttl_sec: 1200, flush_interval_ms: 500,
  }, display: { panel_fields: [] },
};
context.fillForm(state);
let result = context.readForm().streaming;
assert.equal(result.body.light_color, "#A1B2C3");
assert.equal(result.body.dark_color, "#334455");
assert.equal(result.body.text_size, "normal_v2");
assert.equal(result.footer.text_size, "notation");
assert.equal(result.header.template, "violet");
assert.equal(result.process_border, true);
assert.equal(result.card_ttl_sec, 1200);
for (const value of ["", "0", "-1", "86401", "NaN", "Infinity"]) {
  control("f-card-ttl-sec").value = value;
  assert.equal(context.validateCardTTL(), false, value);
  assert.equal(control("f-card-ttl-sec").attrs["aria-invalid"], "true");
  assert.equal(control("error-card-ttl").hidden, false);
}
for (const value of ["1", "600", "86400"]) {
  control("f-card-ttl-sec").value = value;
  assert.equal(context.validateCardTTL(), true, value);
  assert.equal(control("error-card-ttl").hidden, true);
}
for (const lang of ["en", "zh"]) {
  context.I18N.setLang(lang);
  for (const key of [
    "hint.body-colors", "hint.header-template", "hint.process-border", "hint.card-ttl", "error.card-ttl",
  ]) {
    assert.notEqual(context.I18N.t(key), key);
  }
}
console.log("form mapping, TTL validation, bilingual help passed");
'''
        result = subprocess.run(
            [node, "-e", script, str(srv._WEB_ROOT / "js/app.js"), str(srv._WEB_ROOT / "js/i18n.js")],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("script", ["app.js", "i18n.js"])
    def test_javascript_syntax(self, script: str) -> None:
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is required for JavaScript syntax checks")
        assert node is not None
        result = subprocess.run([node, "--check", str(srv._WEB_ROOT / "js" / script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


class TestAllowedHostsGate:
    """Host 门白名单配置化（studio.allowed_hosts）— 默认仅 loopback，配置后放行网段."""

    @staticmethod
    def _host_status(server: str, request_host: str) -> int:
        host, port = server.removeprefix("http://").split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        try:
            conn.putrequest("GET", "/", skip_host=True)
            conn.putheader("Host", request_host)
            conn.endheaders()
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()

    def test_allowlist_refreshes_each_request(self, server: str, home: Path) -> None:
        assert self._host_status(server, "studio.example") == 403
        srv.write_atomic(home / "config.yaml", {"studio": {"allowed_hosts": ["studio.example"]}})
        assert self._host_status(server, "studio.example") == 200
        srv.write_atomic(home / "config.yaml", {"studio": {"allowed_hosts": ["next.example"]}})
        assert self._host_status(server, "studio.example") == 403
        assert self._host_status(server, "next.example") == 200
        for host in ("127.0.0.1", "localhost:8765", "[::1]:8765"):
            assert self._host_status(server, host) == 200

    def test_allowlist_follows_active_home(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        srv.write_atomic(home / "config.yaml", {"studio": {"allowed_hosts": ["first.example"]}})
        assert "first.example" in srv._allowed_hosts()
        other = home / "other"
        other.mkdir()
        srv.write_atomic(other / "config.yaml", {"studio": {"allowed_hosts": ["second.example"]}})
        monkeypatch.setattr(srv, "hermes_home", lambda: other)
        assert "second.example" in srv._allowed_hosts()
        assert "first.example" not in srv._allowed_hosts()

    def test_lan_host_blocked_by_default(self, server: str) -> None:
        host, port = server.removeprefix("http://").split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        conn.putrequest("GET", "/api/state", skip_host=True)
        conn.putheader("Host", f"192.168.31.77:{port}")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()
        conn.close()

    def test_lan_host_allowed_via_config(self, server: str, home: Path) -> None:
        srv.write_atomic(home / "config.yaml", {"studio": {"allowed_hosts": ["192.168.31."]}})
        host, port = server.removeprefix("http://").split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        conn.putrequest("GET", "/api/state", skip_host=True)
        conn.putheader("Host", f"192.168.31.77:{port}")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()
        conn.close()
