## 🍟 hermes-fry-cards v0.5.3

> 拆卡后工具面板不再反复报 300313 抖动；排队续轮的回复也走卡片了。**两个真实环境暴露的
> 问题，一次修掉**——都出在长回复 / 高频排队这类压力场景。

### 🐛 拆卡抖动（#34）

长回复触发拆卡后，工具面板在后续每次 flush 里反复报 `300313 not find elementID : tools_N`，
随后回滚重建、几秒后再失败，形成「失败 → 重建 → 再失败」的循环。本机实测该报错 10-13 起
51 次、10-14 57 次，全部出现在拆卡之后。

根因：拆段给新段分配的 `tools_N` 与共享面板 `tool_panel` 不一致——卡片上根本没有这个元素。

- `split_tool_segment` 拆出的段沿用共享面板 `tool_panel`，与 `_new_tool` 对齐
- dirty 分支兜底改用 `TOOL_PANEL_ELEMENT_ID`，避免将来新路径再指向不存在的元素

### 🐛 排队续轮回退纯文本（#35）

后台通知等以「排队续轮」形态进来的轮次，回复不走卡片、只能回退纯文本。

**修正了 issue 里的诊断。** 原判据说续轮「对插件完全不可见」、按轮次钩子全在
`_handle_message_with_agent`——这不成立：流式钩子挂在 `TurnRunner` 上，而 `_run_agent`
每次调用都会重建 `TurnRunner`，递归续轮照样进钩子。真正缺的是**续轮那一轮没有自己的
COMPLETE 钩子**（那个注入在续轮绕过的 `_handle_message_with_agent` 里），于是卡片停在
「处理中」，回复又按默认路径发成纯文本。

- 新增 `on_queued_followup_completed`：封续轮自己的卡，并写回 `already_sent` / `final_response` 抑制重复投递
- **封卡失败则放行纯文本**，避免整轮静默
- 无 inbound id 的无事件续轮（后台通知 / steer）落到合成卡 `syn-*`

### ✅ 验证

- **对着真实 Hermes v0.22.0 源码**跑完整 patcher 流程：锚点齐全、产出合法 Python、
  每个 marker 恰好一次、重复 apply 幂等、`remove` 逐字节还原
- 新增 `test_patcher_real_v022.py` 锁住注入锚点，并验证它**真的会失败**：改掉锚点模拟上游
  漂移后 4 条全 failed
- 全量 **830 passed**（基线 816 + 14 条新测试），新测试 revert 后 RED 确认

### 📦 升级

**PyPI（推荐）**：

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards install
hermes gateway restart
```

**源码方式**：

```bash
cd ~/projects/hermes-fry-cards && git pull
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U .
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards install
hermes gateway restart
```

> ⚠️ 本版**新增了一个注入 marker**（`FOLLOWUP_DONE`）：老的注入块不会自动迁移，所以
> `uninstall` + `install` 这一步**必须执行**，不能只更新包。

### 🔍 升级后自查

| 现象 | 含义 |
| --- | --- |
| `injected hook failed: followup_done` | 新钩子抛异常，日志里有完整栈 |
| 排队续轮出现 `card_created`（`synthetic=1`） | 无事件续轮正确落到合成卡 |
| 不再有 `final text delivery not confirmed` | 纯文本重复投递已抑制 |
| 不再有 `300313 not find elementID : tools_N` | 拆卡抖动已消除 |

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.5.2...v0.5.3](https://github.com/techysy/hermes-fry-cards/compare/v0.5.2...v0.5.3)
