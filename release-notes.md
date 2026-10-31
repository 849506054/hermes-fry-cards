## 🍟 hermes-fry-cards v0.6.1

> 小版本：委派（子任务）完成回执不再上屏网关内部标记行（#54）；重建缺失的
> `tools/clarify_gateway.py`，修复测试套件在任意新 checkout 上收集即失败的问题。

### 🧾 委派完成回执的兜底文案（#54）

委派类后台完成事件（`delegate_task` 子任务批次完成）走 `BG_COMPLETION` 通道时，
`command` / `started_at` / `exit_code` / `session_id` 四个字段全空，
`background_completion_text` 落到兜底分支：去包裹取首行。但委派通知不是
`[IMPORTANT: …]` 形态，拆完首行仍是网关内部标记 `[ASYNC DELEGATION BATCH COMPLETE — …]`；
回执卡按设计不带 header，客户端点开就只有这行内部文案：

- **内部标记不上屏**：兜底首行形如 `[大写标记 …]`（`[ASYNC …]`、`[BG_JOB]` 这类，
  标记词全大写、可带 `_`/数字；`[2026-10-30 …]` 时间戳以数字开头不会命中）或含
  `BATCH COMPLETE` 时，改给通用句 `✅ 后台任务已完成`
- **既有兜底不变**：shell 后台任务的 `[IMPORTANT: …]` 拆包裹首行照旧透传
  （那条 `Background process proc_x completed normally …`）
- 方案 2（`BG_COMPLETION` 注入点给委派事件补面向用户的字段）留作后续，需要展示
  子任务名 / 条数 / 耗时再动注入点

### 🧪 测试套件修复：重建 tools/clarify_gateway.py

`tests/test_clarify_buttons.py` 随 0f3bf76 入库，但它依赖的 `tools/clarify_gateway.py`
从未提交——任何新 checkout 上全量 pytest 收集即失败（`No module named 'tools'`）。
按 `tests/samples/` 归档的真实 Hermes 源码用法（`run_turn_runner.py` / 单文件
`run.py`）+ 插件侧 `clarify.py` 调用点反推 API 契约重建：`register` /
`wait_for_response` / `resolve_gateway_clarify` / `get_pending_for_session` /
`resolve_text_response_for_session` / `mark_awaiting_text` / `clear_session` /
`get_clarify_timeout` 与 `CANCELLED` / `SKIPPED` 哨兵，及插件直接访问的 `_lock` /
`_entries`。模块注明为随仓参考实现：部署环境由 Hermes 自带 tools 包提供，插件缺失时
整体降级。`conftest.py` 把仓库根加入 sys.path，裸 pytest 也能跑。

### ⚙️ 其他

- 全量 **1062 passed**，ruff / mypy 相对 v0.6.0 零新增

### 📦 升级

本版**无新增注入点、无配置变更**，只升级包 + 重启网关即可；重跑 `uninstall + install`
亦无副作用。

**PyPI（推荐）**：

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards
hermes gateway restart
```

**源码方式**：

```bash
cd ~/projects/hermes-fry-cards && git pull
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U .
hermes gateway restart
```

### 🔍 升级后自查

| 现象 | 含义 |
| --- | --- |
| 派一个后台子任务、不打断，等它完成 | 回执卡正文是 `✅ 后台任务已完成`（shell 后台任务仍是 `✅ 后台任务已完成 — \`命令\` (Ns)`），不再是 `[ASYNC DELEGATION BATCH COMPLETE — …]` |
| 开发机全量测试 | 干净 checkout 上 `python -m pytest tests/ -q` 能正常收集并跑通（1062 passed），不再 `No module named 'tools'` |

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.6.0...v0.6.1](https://github.com/techysy/hermes-fry-cards/compare/v0.6.0...v0.6.1)
