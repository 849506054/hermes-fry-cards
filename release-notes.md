## 🍟 hermes-fry-cards v0.5.6

> 工具密集的长轮次不再「静默涨到飞书 200 元素上限 → 拆卡 → 整轮回落纯文本」；
> 忙碌回执、重定向、后台回执不再另发一条纯文本，改为写进当前卡片。
> **一次记修复 + 一次提示入卡。**

### 🐛 工具面板元素记账（#41）

长工具轮次里卡片元素数会悄悄涨到上限，下一个正文元素写入就报 300305，随后强制拆卡又撞上限，
三次重试失败后整轮回落纯文本。根因是共享工具面板的记账四处失真：

- **面板增量恒为 0**：`_do_flush` 提前推进 `tool_panel_estimate`，批更新前读到的已是新值。
  改为只在 `_do_batch_update` 成功后推进
- **拆卡后新卡重发旧卡步骤**：面板渲染区间写死从第 0 步起。新增 `panel_steps_start()`，三处分支
  统一取本卡首个工具段
- **强制拆卡不推进 `split_index`**：完成态按 `active_segments()` 重建，把旧卡内容再渲染一遍。
  300305 后 `split_index` 前移到首个未创建 segment
- **拆卡判断重复计入面板**：以恒为 0 的 `seg.element_estimate` 为基准，约 17 步就拆卡。改为以
  本卡已入账的面板总量为基准

另修两处同链缺口：面板占满时按 step 边界强制拆（本卡至少保留 1 个 step）；拆卡前容量判断按
「替换后的面板估算」计算并扣掉旧面板，不再拆出只有 loading 的空白卡。

### ✨ 提示消息入卡（#43）

Hermes 在回合进行中会自己合成状态提示。此前它们以纯文本另发一条，重定向时还落在旧卡上：

- **通知区**：完成态卡片顶部 `💡 提示`，内容就是 Hermes 原文，插件不另写文案；流式态建卡带最新一条
- **归属解析**沿用现有 `_sessions` / `_session_keys`：`message_id` → `session_key` → 唯一活跃卡；
  多卡且无标识时放弃，让 Hermes 照常发原文（宁可重复，不误写别人的卡）
- **重定向**沿用 `on_interrupted` + `_interrupt_map` 换卡：旧卡收尾、新卡接手，完成回调仍落到新卡
- **后台回执**：`reply_to_message_id` 命中活跃卡时并入，否则保持原静态卡
- 新增 `BUSY_ACK` / `REDIRECT` 两个**可选注入点**：旧版 Hermes 缺锚点时跳过注入，`verify` 不报错
- 通知元素 id 按序号派生（`fry_notice`、`fry_notice_1`…），并计入 `element_count`

### ⚠️ 尚未覆盖

- 网关重启时把遗留的活跃卡封成「已中断」（需要跨进程登记）
- 独立的后台完成通知注入点（目前只在 `reply_to_message_id` 命中活跃卡时并入）
- `busy_steer_ack_enabled: false` 的实际行为尚未实测

### ✅ 验证

- 全量 **873 passed**（新增 `tests/test_element_budget.py`、`tests/test_notice_in_card.py`）
- 真实 Hermes v0.22.0 源码上的锚点位置与 `apply` → `remove` 逐字还原
- 已在 Hermes v0.22.0 + systemd `hermes-gateway` 上 `verify` / `uninstall` / `install` / 重启，
  `busy_ack` / `redirect` 两个新注入点均落位，网关运行无注入相关报错

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

> ⚠️ 本版新增两个注入点（`BUSY_ACK` / `REDIRECT`），`uninstall` + `install` 这一步**必须执行**，
> 不能只更新包——否则新注入点不会写进 `gateway/run_busy.py`。

### 🔍 升级后自查

| 现象 | 含义 |
| --- | --- |
| `status` 里有 `busy_ack: installed`、`redirect: installed` | 新注入点已落位 |
| 忙碌时不再另发一条纯文本，卡片顶部出现 `💡 提示` | 提示已入卡 |
| 长工具轮次日志出现 `CardKit split: ... split_idx=N` | 主动拆卡按记账正常触发 |
| 仍然出现 `code=300305` | 记账仍有偏差，请附 `elements=` / `new_est=` 日志反馈 |

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.5.5...v0.5.6](https://github.com/techysy/hermes-fry-cards/compare/v0.5.5...v0.5.6)
