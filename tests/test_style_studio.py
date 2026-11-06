"""Focused Studio coverage for registry styles, resets, snapshots and native DOM."""
from __future__ import annotations

import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from hermes_fry_cards.studio import server as srv
from hermes_fry_cards.style_options import STYLE_FIELDS, style_schema, validate_style_role


def samples() -> dict:
    values = {}
    examples = {
        "hex_color": "#A1B2C3", "padding": "4px 8px", "margin": "2px 4px",
        "radius": "9px", "icon_size": "20px 20px", "icon_token": "info_outlined",
        "img_key": "img_studio_test", "separator": " | ", "row_max_height": "120px",
    }
    for role, group in style_schema().items():
        row = {}
        for field, spec in group["fields"].items():
            if field == "*":
                row["read_file"] = ({"default": "normal", "pc": "heading", "mobile": "small"}
                                    if role == "text_sizes" else "info_outlined")
            elif spec["kind"] in examples:
                row[field] = examples[spec["kind"]]
            elif spec.get("choices"):
                row[field] = spec["choices"][-1]
            elif spec["type"] == "boolean":
                row[field] = True
            else:
                row[field] = spec["min"]
        values[role] = row
    return values


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(srv, "_collect_status", lambda _home: {})
    (tmp_path / "config.yaml").write_text("agents:\n  keep: true\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("role", list(STYLE_FIELDS))
def test_every_registry_field_validates_merges_and_reads(role: str, isolated_home: Path) -> None:
    fields = samples()[role]
    cleaned = srv.validate_payload({"streaming": {role: fields}})
    assert cleaned["streaming"][role] == validate_style_role(role, fields)
    base = {"agents": {"keep": True}, "streaming": {role: {"extension": "preserve"}}}
    before = deepcopy(base)
    merged, changed = srv.merge_managed(base, cleaned)
    assert base == before
    assert merged["streaming"][role]["extension"] == "preserve"
    assert len(changed) == len(fields)
    srv.write_atomic(isolated_home / "config.yaml", merged)
    state = srv.collect_state(isolated_home)
    assert state["style_schema"] == style_schema()
    for field, value in fields.items():
        assert state["streaming"][role][field] == value


def test_optional_reset_preserves_unrelated_fields(isolated_home: Path) -> None:
    base = {"streaming": {
        "header": {"padding": "8px", "template": "blue", "custom": 7},
        "body": {"bold": True, "margin": "5px"},
        "tool_icons": {"read_file": "info_outlined", "terminal": "setting_outlined"},
    }, "agents": {"keep": True}}
    reset = srv.validate_payload({"streaming": {
        "header": {"padding": None}, "body": {"bold": None}, "tool_icons": {"read_file": None},
    }})
    merged, changed = srv.merge_managed(base, reset)
    assert merged["streaming"]["header"] == {"template": "blue", "custom": 7}
    assert merged["streaming"]["body"] == {"margin": "5px"}
    assert merged["streaming"]["tool_icons"] == {"terminal": "setting_outlined"}
    assert len(changed) == 3
    assert base["streaming"]["body"]["bold"] is True
    vals = srv._apply_overrides({"streaming": base["streaming"], "display": {}}, reset)
    assert "padding" not in vals["streaming"]["header"]
    srv.write_atomic(isolated_home / "config.yaml", merged)
    assert "bold" not in srv.collect_state(isolated_home)["streaming"]["body"]


@pytest.mark.parametrize("streaming", [
    {"body": {"not_a_field": None}}, {"body": {"__proto__": {"polluted": True}}},
    {"tool_icons": {"constructor": "info_outlined"}}, {"tool_icons": {"a b": None}},
    {"process": {"padding": "url(https://example.com)"}},
    {"table": {"page_size": True}}, {"tool_icons": {"safe": {"prototype": 1}}},
    {"header": {"font_family": "serif"}}, {"header": {"padding": "100px"}},
])
def test_invalid_styles_and_prototype_keys_rejected(streaming: dict) -> None:
    with pytest.raises(ValueError):
        srv.validate_payload({"streaming": streaming})


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


@pytest.mark.parametrize("state", ["streaming", "completed"])
def test_real_builder_preview_isolated_styles_and_reset(isolated_home: Path, state: str) -> None:
    before = (isolated_home / "config.yaml").read_bytes()
    overrides = {"streaming": {
        "header": {"padding": "3px", "icon_token": "info_outlined"},
        "body": {"body_padding": "7px", "text_align": "right", "surface_margin": "2px"},
        "process": {"header_padding": "5px", "border_radius": "11px"},
        "process_border": True,
        "loading": {"img_key": "img_studio_test", "size": "20px 20px"},
        "table": {"column_width": "120px", "header_text_size": "heading"},
        "footer": {"enabled": True, "text_align": "center"},
    }, "display": {"unified_panel_min_duration": 0, "show_reasoning": True}}
    card = srv.build_preview({"state": state, "overrides": overrides}, isolated_home)["card"]
    assert card["header"]["padding"] == "3px"
    assert card["header"]["icon"]["token"] == "info_outlined"
    assert card["body"]["padding"] == "7px"
    nodes = list(walk(card))
    assert any(n.get("tag") == "collapsible_panel" and n["header"].get("padding") == "5px" for n in nodes)
    if state == "streaming":
        assert any(n.get("img_key") == "img_studio_test" for n in nodes)
    else:
        assert any(n.get("tag") == "table" and n["columns"][0]["width"] == "120px" for n in nodes)
        assert any(n.get("text_align") == "center" for n in nodes)
    assert (isolated_home / "config.yaml").read_bytes() == before
    srv.write_atomic(isolated_home / "config.yaml", {"streaming": {"header": {"padding": "8px"}}})
    reset = srv.build_preview(
        {"state": state, "overrides": {"streaming": {"header": {"padding": None}}}}, isolated_home,
    )
    assert "padding" not in reset["card"]["header"]
    assert yaml.safe_load((isolated_home / "config.yaml").read_text())["streaming"]["header"]["padding"] == "8px"


@pytest.mark.parametrize("state", ["streaming", "completed"])
def test_clarify_preview_uses_shared_builder(isolated_home: Path, state: str) -> None:
    card = srv.build_preview({"scenario": "clarify", "state": state, "overrides": {"streaming": {
        "clarify": {"pending_template": "purple", "completed_template": "orange"},
    }}}, isolated_home)["card"]
    assert card["header"]["template"] == ("purple" if state == "streaming" else "orange")
    assert card["elements"]


def test_clarify_custom_input_styles(isolated_home: Path) -> None:
    card = srv.build_preview({"scenario": "clarify_input", "state": "streaming",
                              "overrides": {"streaming": {"clarify": {"active_hint_color": "purple"}}}},
                             isolated_home)["card"]
    assert any(node.get("tag") == "form" for node in walk(card))
    assert "purple" in card["elements"][0]["content"]


def node_run(script: str, *args: str) -> None:
    node = shutil.which("node")
    assert node, "Node is required for Studio DOM checks"
    result = subprocess.run([node, "-e", script, *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", ["app.js", "i18n.js", "preview.js"])
def test_javascript_syntax(script: str) -> None:
    result = subprocess.run(["node", "--check", str(srv._WEB_ROOT / "js" / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_registry_editor_dom_mapping_and_labels() -> None:
    node_run(r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const nodes = new Map();
class Element {
  constructor(tag) { this.tagName=tag.toUpperCase(); this.children=[]; this.options=[]; this.dataset={};
    this.style={}; this.attrs={}; this.value=''; this.validity=''; }
  set id(id) { this._id=id; nodes.set(id,this); } get id() { return this._id; }
  set innerHTML(value) { this.children=[]; } get innerHTML() { return ''; }
  appendChild(child) { this.children.push(child); child.parent=this;
    if (child.tagName==='OPTION') this.options.push(child); }
  setAttribute(k,v) { this.attrs[k]=v; } getAttribute(k) { return this.attrs[k]; }
  addEventListener() {} querySelectorAll() { return []; }
  setCustomValidity(v) { this.validity=v; } checkValidity() { return !this.validity; }
  closest() { return this.parent.parent.parent; }
}
function control(id) { if (!nodes.has(id)) { const n=new Element('div'); n.id=id; } return nodes.get(id); }
global.window={}; global.location={search:'',hash:''}; global.localStorage={getItem(){return null;},setItem(){}};
global.document={createElement: t=>new Element(t),getElementById:control,querySelectorAll(){return [];},
  documentElement:new Element('html')};
vm.runInThisContext(fs.readFileSync(process.argv[2],'utf8'));
global.I18N=window.FryI18n; global.$=sel=>control(sel.slice(1)); global.toast=()=>{};
const app=fs.readFileSync(process.argv[1],'utf8');
const start=app.indexOf('  /* ---------- Registry-driven optional styles');
const end=app.indexOf('  async function loadState',start);
vm.runInThisContext(app.slice(start,end));
const schema=JSON.parse(process.argv[3]), values=JSON.parse(process.argv[4]);
renderStyleFields({style_schema:schema,streaming:values});
let expected=0;
for (const role of Object.keys(schema)) {
  for (const field of Object.keys(schema[role].fields)) {
    if ((legacyStyleFields[role]||[]).includes(field)) continue;
    expected++;
    assert.ok(nodes.has('style-'+role+'-'+field), role+'.'+field);
  }
}
assert.equal(styleControls.length,expected);
for (const lang of ['zh','en']) {
 I18N.setLang(lang);
 for (const role of Object.keys(schema)) assert.notEqual(I18N.t('style.role.'+role),'style.role.'+role);
 for (const item of styleControls) {
  const key='style.field.'+(item.field==='*'?'mapping':item.field);
  assert.notEqual(I18N.t(key),key);
  assert.ok(item.input.attrs['aria-describedby'].includes(item.input.id+'-error'));
 }
}
const streaming={}; readStyleFields(streaming);
for (const item of styleControls) {
 if (item.isMap) assert.deepEqual({...streaming[item.role]},values[item.role]);
 else assert.equal(streaming[item.role][item.field], values[item.role][item.field]);
}
const separator=styleControls.find(x=>x.field==='separator');
separator.input.value='""'; assert.equal(parseStyleControl(separator),'');
separator.input.value=''; assert.equal(parseStyleControl(separator),null);
const bold=styleControls.find(x=>x.role==='body'&&x.field==='bold'); bold.input.value='';
const map=styleControls.find(x=>x.isMap); map.input.value='{}';
const reset={}; readStyleFields(reset);
assert.equal(reset.body.bold,null); assert.equal(reset.tool_icons.read_file,null);
map.input.value='{"__proto__":"info_outlined"}'; assert.equal(validateStyleControl(map),false);
map.input.value='{"read_file":123}'; assert.equal(validateStyleControl(map),false);
map.input.value='{"read_file":"info_outlined"}'; assert.equal(validateStyleControl(map),true);
const padding=styleControls.find(x=>x.role==='header'&&x.field==='padding');
assert.equal(padding.input.type,'text'); padding.input.value='2px 4px';
assert.equal(parseStyleControl(padding),'2px 4px');
const angle=styleControls.find(x=>x.field==='icon_expanded_angle');
assert.equal(typeof parseStyleControl(angle),'number');
const color=styleControls.find(x=>x.role==='body'&&x.field==='text_color'); assert.equal(color.input.type,'text');
color.input.value='rgba(1,2,3,1)'; assert.equal(parseStyleControl(color),'rgba(1,2,3,1)');
console.log('editor fields:',expected);
''', str(srv._WEB_ROOT / "js/app.js"), str(srv._WEB_ROOT / "js/i18n.js"),
        json.dumps(style_schema()), json.dumps(samples()))


def test_native_preview_dom_styles_and_tables() -> None:
    node_run(r'''
const assert = require('assert/strict');
let theme='light';
global.window={}; global.document={documentElement:{getAttribute(){return theme;}},createElement(tag){return {
 tag,style:{},children:[],appendChild(child){this.children.push(child);},addEventListener(){},
};}};
require(process.argv[1]);
const card={header:{title:{content:'Title'},padding:'3px',icon:{token:'info_outlined',color:'red'}},
 config:{style:{color:{'cus-0':{light_mode:'rgba(1,2,3,1)',dark_mode:'rgba(4,5,6,1)'}}}},
 body:{padding:'7px',vertical_spacing:'8px',elements:[
  {tag:'column_set',background_style:'cus-0',padding:'4px',margin:'2px',columns:[{padding:'10px',vertical_spacing:'12px',elements:[
   {tag:'markdown',content:'**bold** and *italic*',text_align:'right',text_size:'heading',margin:'5px'}]}]},
  {tag:'collapsible_panel',expanded:true,padding:'6px',border:{color:'red',corner_radius:'11px'},
   header:{padding:'9px',position:'bottom',icon:{token:'down_outlined',size:'20px 20px'},icon_expanded_angle:90,
    title:{tag:'plain_text',content:'Process',text_size:'heading',text_color:'green'}},elements:[]},
  {tag:'table',page_size:1,row_height:'high',row_max_height:'80px',freeze_first_column:true,
    header_style:{text_align:'center',text_size:'heading',bold:false},
    columns:[{name:'c0',display_name:'Name',width:'120px',horizontal_align:'right'}],rows:[{c0:'hello'},{c0:'second'}]},
  {tag:'markdown',content:'footer',text_align:'center',margin:'2px'},
 ]}};
const root=window.FryPreview.renderCard(card), body=root.children[1];
assert.equal(root.children[0].className,'card-header t-default');
assert.equal(root.children[0].style.padding,'3px'); assert.equal(root.children[0].children[0].title,'info_outlined');
assert.equal(body.style.padding,'7px'); assert.equal(body.style.rowGap,'8px');
const surface=body.children[0]; assert.equal(surface.style.backgroundColor,'rgba(1,2,3,1)');
assert.equal(surface.style.margin,'2px'); assert.equal(surface.style.padding,'10px');
assert.equal(surface.style.rowGap,'12px'); assert.equal(surface.children[0].style.textAlign,'right');
assert.equal(surface.children[0].style.fontSize,'16px');
assert.ok(surface.children[0].children[0].innerHTML.includes('<i>italic</i>'));
const panel=body.children[1]; assert.equal(panel.style.borderRadius,'11px');
assert.ok(panel.className.includes('cp-bottom'));
assert.equal(panel.children[0].style.padding,'9px');
assert.equal(panel.children[0].children[0].style.transform,'rotate(90deg)');
assert.equal(panel.children[0].children[1].style.fontSize,'16px');
const table=body.children[2].children[0]; assert.equal(table.tag,'table');
assert.ok(table.className.includes('freeze-first'));
const th=table.children[0].children[0].children[0]; assert.equal(th.style.width,'120px');
assert.equal(th.style.textAlign,'center');
assert.equal(th.style.fontWeight,'400'); assert.equal(table.children[1].children.length,1);
assert.equal(table.children[1].children[0].children[0].style.textAlign,'right');
assert.equal(body.children[3].style.textAlign,'center');
theme='dark';
assert.equal(window.FryPreview.renderCard(card).children[1].children[0].style.backgroundColor,'rgba(4,5,6,1)');
assert.ok(window.FryPreview.renderMarkdown('# Preserved heading').startsWith('<h1>'));
assert.ok(window.FryPreview.renderMarkdown('a\n\n\n\nb').includes('height:2em'));
assert.ok(window.FryPreview.renderMarkdown('`**code**`').includes('<code>**code**</code>'));
assert.ok(!window.FryPreview.renderMarkdown('<script>alert(1)</script>').includes('<script>'));
const legacy=window.FryPreview.renderCard({elements:[
 {tag:'action',actions:[{tag:'button',type:'danger',text:{content:'choose'}}]}]});
assert.equal(legacy.children[0].children[0].children[0].disabled,true);
console.log('native DOM styles, themes, table and safe legacy actions passed');
''', str(srv._WEB_ROOT / "js/preview.js"))
