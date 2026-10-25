## 🍟 hermes-fry-cards v0.5.10

> #47 / #49 / #50 实测反馈三连修（修复实现采纳自测试方本地线 `local/0.5.9.4`，cherry-pick 保留原作者）：
> 一次重定向不再建两张卡、提示不再残留封卡正文、后台完成回执不再上屏内部文案、
> 卡外不再多一条纯文本补发。

### 🔁 一次重定向只换一次卡（#49）

REDIRECT 与 REDIRECT_RESOLVED 两个注入点对同一事件各调一次 `on_interrupted`，第二次到达时
`old_message_id` 已被换成刚建好的新会话（`old == new`），0.5.9 的幂等判据分不出来，于是建出
第二个会话、提示写进随后被 dispose 的那个：

- 按 `new_message_id` 的 2 秒去重窗口：重复调用记 `interrupt_duplicate_skipped` 直接返回
- 已建新会话命中即复用（`_reuse_interrupted_session` 只补登记、不抢键——anchor 已指向别的
  活跃卡时保持原样）

### 🧵 提示单槽位 + 流式期即时可见 + 封卡让位正文（#50）

完成态卡片正文末尾不再残留「↪ 已重定向…」这类过期提示：

- **单槽位**：后到的提示覆盖先到的，同一时刻卡上最多一条（忙碌 / 重定向 / 后台回执共用）
- **首段正文原地接管**：建卡时提示用真实流式元素（`fry_notice`）承载，首个 answer 段复用同一
  element id 原地覆盖成正文，不再在旁边新增一段
- **封卡剔除**：`drop_rendered_progress_notices` 在封卡渲染前摘掉已画在卡上的提示，位置留给
  正文；本轮没有正文（被打断）时保留
- **流式期即时可见**：flush 新增「通知槽位同步」——回执并入活跃卡后当场补一次元素更新
  （无可复用元素时在 loading 之前补画 `fry_notice_live_N`），不必等封卡重建
- 换卡重建时接管过提示的正文段自动重派元素 id，避免新卡 `fry_notice` 双元素撞 id

> #43 口径调整：完成态通知区从「多条列表分区」变为「单槽位 + 流式即时 + 封卡让位」。

### 🧾 后台完成回执显示与去重（#47）

- **用户文案**：卡片不再上屏发给模型的内部注入文案（`[IMPORTANT: ...]`），改拼
  `✅ 后台任务已完成 — 命令 (Ns)`（命令超 40 字截断，缺字段回退去包裹首行）
- **无标题独立卡**：独立回执卡去掉 header，正文第一行就是内容本身；`config.summary`
  仅作会话列表摘要
- **`BG_BUSY_SUPPRESS` 可选注入点**（新增）：发起轮还在跑时网关 `_run_process_watcher` 的
  turn_busy 分支会补发一条 concise 纯文本——回执已由卡片接管时这条就是卡外多出来的一条。
  注入点挂在 concise 文案合成之后、发送之前，命中登记（session_id / session_key /
  started_at，10 分钟窗口）即跳过补发；拿不到键、过期、禁用一律照常发，功能无损降级

### ✅ 其他

- `tests/conftest.py` 把 `HERMES_HOME` 隔离到临时目录：测试夹具不再污染生产
  `state/fry-cards-live.json`（此前每跑一次套件都会往里写夹具 id）
- 全量 **997 passed**，ruff / mypy 相对 v0.5.9 零新增；`BG_BUSY_SUPPRESS` 真实源码回归
  补归档样张，Windows 开发机也能跑

### ⚠️ 升级必读

本版新增注入点 `BG_BUSY_SUPPRESS`（并变更 `BG_COMPLETION` 钩子参数），**必须重跑
`uninstall` + `install`**，只升级包不会写进 `gateway/run_notifications.py`。

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

### 🔍 升级后自查

| 现象 | 含义 |
| --- | --- |
| `status` 里 `bg_busy_suppress: installed` | 新注入点落位 |
| 发一条消息打断 | 日志只有一条 `interrupt_abort_old` + 一条 `session_created`；快速连点或双注入时多出的那次记 `interrupt_duplicate_skipped` |
| 重定向后新卡 | 提示在流式期可见；封卡后正文末尾没有「↪ 已重定向…」残留 |
| 后台任务完成（无活跃卡） | 独立卡标题就是正文 `✅ 后台任务已完成 — 命令 (Ns)`，卡外不再多一条纯文本，日志有 `busy_receipt_suppressed` |

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.5.9...v0.5.10](https://github.com/techysy/hermes-fry-cards/compare/v0.5.9...v0.5.10)
