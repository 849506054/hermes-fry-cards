## 🍟 hermes-fry-cards v0.5.9

> v0.5.8 实测反馈两连修：合成轮次（后台完成通知触发的回复轮）的 `streamed=False`
> 抑制误报消除（#48）；后台完成通知不再以纯文本气泡直达，改为并入活跃卡或
> 独立静态卡（#47，上版「尚未覆盖」项正式落地）。

### 🚫 合成轮次的抑制误报（#48）

后台完成通知触发的回复轮没有 `event_message_id`，`mark_stream_delivered` 拿到 `None`
后谓词恒 False，网关误打 `Normal final-send NOT suppressed ... streamed=False` 重复投递
警告（实际投递无重复，纯噪音）：

- `mark_stream_delivered` 与 `stream_card_healthy` 均增加 `session_key` 兜底
- syn-* 会话注册时本就同步进了 `_session_keys`，谓词按活跃卡健康状态正确回答

### 🧾 后台完成通知接管（#47）

`gateway/run_notifications.py` 的 watch/completion 通知把完成回执合成内部消息注入，
**纯文本气泡直达**——该路径不经过卡片管线，没有任何钩子可拦（0.5.8 release notes
「尚未覆盖」项）。新增可选注入点 `BG_COMPLETION`，挂在 `_inject_watch_notification`
的内部标记处理之前：

- **会话此刻有活跃卡**（按 `session_key` 解析，其次唯一活跃卡）→ 回执按 #43 通知分区
  并入当前卡（完成后通知置顶）
- **无活跃卡** → 复用合成轮次的静态卡路径（`send_card_to_chat`），不 reply 已过期的
  触发消息（Hermes #52694：evt 里的 message_id 在投递时刻已过期）
- 都不可行（禁用 / 无 chat_id / 投递失败）→ 返回 False，Hermes 照常注入，无损降级

### ⚠️ 升级必读

answer 钩子注入模板有变更（传入 `session_key`），**必须重跑 `uninstall` + `install`**，
只升级包的话 #48 的误报不会消失。

### ✅ 验证

- 全量 **947 passed**，ruff / mypy 相对上一版零新增
- 真实 Hermes **v0.22.5** 源码回归：`BG_COMPLETION` 落在 `_inject_watch_notification`
  内部标记处理之前（真实 `run_notifications.py` 上核过）

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
| `status` 里有 `bg_completion: installed` | 通知接管注入点已落位 |
| 后台完成通知触发的轮次 | 日志不再出现 `Normal final-send NOT suppressed` |
| 后台任务完成、会话无活跃卡 | 回执以独立卡片出现（不再纯文本气泡） |
| 后台任务完成、会话有活跃卡 | 回执出现在卡片顶部通知区 |

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.5.8...v0.5.9](https://github.com/techysy/hermes-fry-cards/compare/v0.5.8...v0.5.9)
