"""Platform text-size maps: validation, frozen cards and Studio roundtrip."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hermes_fry_cards.config import Config
from hermes_fry_cards.studio import server as srv
from hermes_fry_cards.style_options import style_schema, validate_style_role

SIZES = {"reader": {"default": "normal", "pc": "heading-2", "mobile": "large"}}


def write_config(home: Path, streaming: dict) -> None:
    srv.write_atomic(home / "config.yaml", {"streaming": streaming, "agents": {"keep": True}})


def test_same_instance_reload_snapshot_and_reference_filtering(tmp_path):
    write_config(tmp_path, {"text_sizes": SIZES, "body": {"text_size": "reader"},
                            "footer": {"text_size": "reader"}})
    cfg = Config(home=tmp_path)
    old = cfg.card_snapshot()
    assert cfg.style_options("text_sizes") == SIZES
    assert cfg.body_text_size == cfg.footer_text_size == "reader"
    copy = cfg.style_options("text_sizes")
    copy["reader"]["pc"] = "notation"
    assert cfg.style_options("text_sizes") == SIZES
    newer = {"reader": {"default": "notation", "pc": "small", "mobile": "heading"}}
    write_config(tmp_path, {"text_sizes": newer, "body": {"text_size": "reader"}})
    assert cfg.style_options("text_sizes") == newer
    assert old.style_options("text_sizes") == SIZES
    write_config(tmp_path, {"body": {"text_size": "missing", "bold": True},
                            "footer": {"text_size": "missing"},
                            "reasoning": {"text_size": "missing", "margin": "2px"}})
    assert cfg.style_options("body") == {"bold": True}
    assert cfg.style_options("reasoning") == {"margin": "2px"}
    assert cfg.body_text_size == "normal_v2"
    assert cfg.footer_text_size == "notation"
    assert old.body_text_size == "reader"


def test_panel_size_frozen_with_streaming_and_display_switches_live(tmp_path):
    data = {"streaming": {"text_sizes": SIZES}, "display": {
        "panel_text_size": "heading", "platforms": {"feishu": {
            "panel_text_size": "reader", "show_reasoning": True,
            "show_tool_use": True, "panel_fields": ["model"],
        }},
    }}
    srv.write_atomic(tmp_path / "config.yaml", data)
    cfg = Config(home=tmp_path)
    old = cfg.card_snapshot()
    assert old.panel_text_size == "reader"
    data["streaming"]["text_sizes"] = {"small_reader": {"pc": "small"}}
    data["display"]["platforms"]["feishu"].update({
        "panel_text_size": "small_reader", "show_reasoning": False,
        "show_tool_use": False, "panel_fields": ["elapsed"],
    })
    srv.write_atomic(tmp_path / "config.yaml", data)
    assert cfg.panel_text_size == "small_reader"
    assert cfg.card_snapshot().panel_text_size == "small_reader"
    assert old.panel_text_size == old.card_snapshot().panel_text_size == "reader"
    assert old.style_options("text_sizes") == SIZES
    assert old.show_reasoning is False
    assert old.show_tool_use is False
    assert old.panel_fields == ["elapsed"]


def test_config_handler_saves_existing_and_same_payload_size_references(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_collect_status", lambda home: {})
    monkeypatch.setattr(srv, "hermes_home", lambda: tmp_path)
    write_config(tmp_path, {})
    handler = object.__new__(srv.StudioHandler)
    responses = []
    monkeypatch.setattr(handler, "_json", lambda code, obj, extra=None: responses.append((code, obj)))
    handler._handle_config({"streaming": {"text_sizes": SIZES},
                            "display": {"panel_text_size": "reader"}})
    handler._handle_config({"streaming": {"body": {"text_size": "reader"}}})
    cfg = Config(home=tmp_path)
    assert cfg.body_text_size == cfg.panel_text_size == "reader"
    assert responses[-1][0] == 200
    before = (tmp_path / "config.yaml").read_bytes()
    with pytest.raises(ValueError):
        handler._handle_config({"streaming": {"text_sizes": {"reader": None},
                                             "body": {"text_size": "normal"}}})
    with pytest.raises(ValueError):
        handler._handle_config({"display": {"panel_text_size": "undefined"}})
    assert (tmp_path / "config.yaml").read_bytes() == before
    result = srv.build_preview({"overrides": {"display": {"panel_text_size": "reader"}}}, tmp_path)
    assert result["card"]["config"]["style"]["text_size"] == SIZES


def test_mapping_schema_and_valid_references():
    schema = style_schema()
    assert schema["text_sizes"]["dynamic"] is True
    assert schema["text_sizes"]["fields"]["*"]["type"] == "object"
    assert validate_style_role("text_sizes", SIZES) == SIZES
    assert validate_style_role("body", {"text_size": "reader"}, text_sizes=SIZES) == {"text_size": "reader"}
    assert validate_style_role("text_sizes", {"fallback": {"default": "medium"}})
    with pytest.raises(ValueError):
        validate_style_role("table", {"header_text_size": "reader"}, text_sizes=SIZES)


@pytest.mark.parametrize("bad", [
    {"__proto__": {"pc": "normal"}}, {"normal": {"pc": "normal"}},
    {"bad name": {"pc": "normal"}}, {"x": {"pc": "14px"}},
    {"x": {"pc": "normal_v2"}}, {"x": {"pc": "reader"}},
    {"x": {"android": "normal"}}, {"x": {}}, {"x": "normal"},
    {"x": {"default": 14}},
])
def test_invalid_mapping_entries_are_rejected_and_isolated(bad):
    with pytest.raises(ValueError):
        validate_style_role("text_sizes", bad)
    assert validate_style_role("text_sizes", {**bad, **SIZES}, strict=False) == SIZES


def test_studio_roundtrip_merged_references_and_resets(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_collect_status", lambda home: {})
    write_config(tmp_path, {})
    payload = {"streaming": {"body": {"text_size": "reader"}, "text_sizes": SIZES,
                              "reasoning": {"text_size": "reader"}}}
    validated = srv.validate_payload(payload)
    base = srv.read_full_config(tmp_path / "config.yaml")
    merged, changed = srv.merge_managed(base, validated)
    srv.write_atomic(tmp_path / "config.yaml", merged)
    state = srv.collect_state(tmp_path)
    assert state["streaming"]["text_sizes"] == SIZES
    assert state["streaming"]["body"]["text_size"] == "reader"
    assert merged["agents"] == {"keep": True}
    assert "streaming.text_sizes.reader" in changed
    vals = {"streaming": state["streaming"], "display": state["display"]}
    out = srv._apply_overrides(vals, {"streaming": {"footer": {"text_size": "reader"}}})
    assert out["streaming"]["footer"]["text_size"] == "reader"
    with pytest.raises(ValueError):
        srv.validate_payload({"streaming": {"body": {"text_size": "missing"}}})
    with pytest.raises(ValueError):
        srv._apply_overrides(vals, {"streaming": {"text_sizes": {"reader": None}}})
    reset = srv._apply_overrides(vals, {"streaming": {
        "text_sizes": {"reader": None}, "body": {"text_size": "normal"},
        "reasoning": {"text_size": None},
    }})
    assert reset["streaming"]["text_sizes"] == {}
    assert "text_size" not in reset["streaming"]["reasoning"]
    assert vals["streaming"]["text_sizes"] == SIZES


def test_studio_preview_emits_official_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_collect_status", lambda home: {})
    write_config(tmp_path, {"text_sizes": SIZES})
    before = (tmp_path / "config.yaml").read_bytes()
    result = srv.build_preview({"overrides": {"streaming": {"body": {"text_size": "reader"}}}}, tmp_path)
    assert result["card"]["config"]["style"]["text_size"] == SIZES
    assert (tmp_path / "config.yaml").read_bytes() == before


def test_studio_editor_custom_sizes_and_roundtrip():
    script = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert/strict'), nodes=new Map();
class Element {
 constructor(tag) {
  this.tagName=tag.toUpperCase();this.children=[];this.options=[];this.dataset={};this.attrs={};this.value='';
 }
 set id(v){this._id=v;nodes.set(v,this);} get id(){return this._id;}
 set innerHTML(v){this.children=[];this.options=[];} get innerHTML(){return '';}
 appendChild(c){this.children.push(c);c.parent=this;if(c.tagName==='OPTION')this.options.push(c);}
 setAttribute(k,v){this.attrs[k]=v;} getAttribute(k){return this.attrs[k];}
 addEventListener(){} querySelectorAll(){return [];}
 setCustomValidity(v){this.validity=v;} checkValidity(){return !this.validity;}
 closest(){return this.parent.parent.parent;}
}
function control(id){if(!nodes.has(id)){const n=new Element('select');n.id=id;}return nodes.get(id);}
global.window={};global.location={search:'',hash:''};global.localStorage={getItem(){return null;},setItem(){}};
global.document={createElement:t=>new Element(t),getElementById:control,querySelectorAll(){return [];},
 documentElement:new Element('html')};
vm.runInThisContext(fs.readFileSync(process.argv[2],'utf8'));
global.I18N=window.FryI18n;global.$=s=>control(s.slice(1));global.toast=()=>{};
const source=fs.readFileSync(process.argv[1],'utf8');
const start=source.indexOf('  /* ---------- Registry-driven optional styles');
vm.runInThisContext(source.slice(start,source.indexOf('  async function loadState',start)));
const schema=JSON.parse(process.argv[3]);
renderStyleFields({style_schema:schema,streaming:{text_sizes:{reader:{default:'normal',pc:'heading-2',mobile:'large'}},body:{text_size:'reader'}},display:{panel_text_size:'reader'}});
const map=styleControls.find(x=>x.role==='text_sizes');
assert.equal(map.input.tagName,'TEXTAREA');
assert.equal(control('f-body-text-size').value,'reader');
assert.equal(control('f-panel-text-size').value,'reader');
for(const id of ['f-body-text-size','f-footer-text-size','f-panel-text-size']) {
 assert.ok(control(id).options.some(o=>o.value==='reader'));
}
assert.equal(parseStyleControl(map).reader.pc,'heading-2');
for(const bad of ['{"__proto__":{"pc":"normal"}}','{"bad":{"pc":"14px"}}','{"bad":{"pc":"normal_v2"}}',
 '{"bad":{"default":"reader"}}','{"normal":{"pc":"normal"}}']){
 map.input.value=bad;assert.equal(validateStyleControl(map),false);
}
map.input.value='{"compact":{"default":"small","pc":"notation","mobile":"normal"}}';
assert.equal(validateStyleControl(map),true);refreshTextSizeChoices();
assert.ok(control('f-body-text-size').options.some(o=>o.value==='compact'));
const out={};readStyleFields(out);
assert.equal(out.text_sizes.reader,null);assert.equal(out.text_sizes.compact.pc,'notation');
for(const lang of ['zh','en']){
 I18N.setLang(lang);
 assert.notEqual(I18N.t('style.role.text_sizes'),'style.role.text_sizes');
 assert.notEqual(I18N.t('style.platform-sizes'),'style.platform-sizes');
}
console.log(JSON.stringify({streaming:{text_sizes:out.text_sizes,body:{text_size:'compact'}},display:{panel_text_size:'compact'}}));
'''
    result = subprocess.run(["node", "-e", script, str(srv._WEB_ROOT / "js/app.js"),
                             str(srv._WEB_ROOT / "js/i18n.js"), json.dumps(style_schema())],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    cleaned = srv.validate_payload(json.loads(result.stdout), base_streaming={"text_sizes": SIZES})
    assert cleaned["streaming"]["text_sizes"]["compact"]["pc"] == "notation"
    assert cleaned["display"]["panel_text_size"] == "compact"


def test_preview_dom_pc_default_and_card_isolation():
    script = r'''
const assert = require('assert/strict');
global.window={}; global.document={documentElement:{getAttribute(){return 'light';}},createElement(tag){return {
 tag,style:{},children:[],appendChild(child){this.children.push(child);},addEventListener(){},
};}};
require(process.argv[1]);
const card={config:{style:{text_size:{reader:{default:'normal',pc:'heading-2',mobile:'large'},
 fallback:{default:'notation'}}}},body:{elements:[
 {tag:'markdown',content:'PC',text_size:'reader'},
 {tag:'collapsible_panel',header:{title:{tag:'plain_text',content:'title',text_size:'fallback'}},elements:[
 {tag:'div',text:{tag:'plain_text',content:'nested',text_size:'reader'}}]},
]}};
const root=window.FryPreview.renderCard(card), body=root.children[0];
assert.equal(body.children[0].style.fontSize,'20px');
assert.equal(body.children[1].children[0].children[0].style.fontSize,'12px');
assert.equal(body.children[1].children[1].children[0].children[0].style.fontSize,'20px');
const other=window.FryPreview.renderCard({body:{elements:[{tag:'markdown',content:'next',text_size:'reader'}]}});
assert.equal(other.children[0].children[0].style.fontSize,undefined);
'''
    result = subprocess.run(["node", "-e", script, str(srv._WEB_ROOT / "js/preview.js")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
