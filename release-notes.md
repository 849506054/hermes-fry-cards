## 🍟 hermes-fry-cards v0.5.4

> 无事件续轮（后台通知 / steer）的回复不再「纯文本 + 合成卡」各发一遍；新版 Hermes 的
> 微模块重构（`run_turn_progress` / `run_turn_persistence`）也能正常 `verify` / `install` 了。
> **0.5.3 续轮修复的收尾补漏 + 一次上游布局适配。**

### 🐛 无事件续轮投递两次（#37）

0.5.3 给排队续轮补上了自己的收尾钩子，但无事件续轮在**流式阶段**没有 inbound id、插件里也
没有会话——`on_answer_delta` 返回 False，Hermes 便按纯文本流式投了出去；等续轮结束再建合成卡，
同一段文字就有了两条投递路径。

- 新增注入点 1c `on_queued_followup_card_ready`：在续轮递归 `_run_agent` **之前**把卡建好，
  流式直接进卡，核心那条纯文本路径不再触发
- 已存在则复用；无消息 id 落合成卡，按与核心 `next_session_key` 同源的 session_key 注册
- `FOLLOWUP_DONE` 收尾时按同一 session_key 复用这张卡，不再另开一张
- 预建失败只降级为原行为（debug 日志），封卡与纯文本放行不受影响

### 🐛 适配 Hermes 微模块重构（#38）

新版 Hermes 把 `progress_callback` 拆到 `gateway/run_turn_progress.py`、`agent:end` 拆到
`gateway/run_turn_persistence.py`，`verify` 报 `Missing modular Hermes injection targets: tool`。

- `progress` / `persistence` 两个模块**按存在与否动态注册**，旧版 Hermes 布局不受影响
- tool 锚点优先 `progress`、回退 `runner`，verify 与注入共用同一解析顺序
- `agent:end` 在 `turn` 与 `persistence` 任一命中即可；`persistence` 无注入点，原样不写回

### ✅ 验证

- 预建顺序（封上一张卡之后、递归 `_run_agent` 之前）由**真实 Hermes v0.22.0 源码** + 手写夹具双覆盖
- 微模块新布局：verify 通过、TOOL 钩子落 progress、marker 恰好一次、apply 幂等、`remove` 逐字节还原
- 全量 **839 passed**（基线 830 + 9 条新测试），lint / mypy 与基线持平

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

> ⚠️ 本版 `FOLLOWUP_COMPLETE` 与 `FOLLOWUP_DONE` 两个**注入块内容都变了**：老的注入块不会
> 自动刷新，所以 `uninstall` + `install` 这一步**必须执行**，不能只更新包。

### 🔍 升级后自查

| 现象 | 含义 |
| --- | --- |
| `verify` 不再报 `Missing modular Hermes injection targets: tool` | 微模块布局已识别 |
| `injected hook failed: followup_complete` | 预建 / 封卡钩子抛异常，日志里有完整栈 |
| `followup card pre-create skipped`（debug） | 预建降级为原行为，回复仍会送达 |
| 无事件续轮只出现一张卡、没有前置纯文本 | 重复投递已消除 |

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.5.3...v0.5.4](https://github.com/techysy/hermes-fry-cards/compare/v0.5.3...v0.5.4)
