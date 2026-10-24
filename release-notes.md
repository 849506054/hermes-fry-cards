## 🍟 hermes-fry-cards v0.5.8

> v0.5.7 实测反馈两连修：同一回合连续第二次打断不再丢卡，后台完成回执的合成 id 不再报
> `target_message_gone`，`streamed=False` 重复投递警告消除，提示区分区渲染；
> 并治理 #46 —— 长任务 10 Hz × 几十 KB 的整卡重绘把飞书客户端（实测主要安卓端）渲染
> 打满直至被系统杀掉的问题。

### 🔁 连续第二次打断换卡（#43 实测反馈）

同一回合里第二次打断时，Hermes 的忙碌回执被 **30 秒防抖**吞掉（`busy_ack_ts` 检查在回执发送
之前直接 return），挂在回执发送点的 REDIRECT 锚点根本走不到——旧卡停在半截，新消息没有卡：

- 新增可选注入点 `REDIRECT_RESOLVED`，挂在 `_steer` 解包之后、防抖之前：重定向判定一成立就换卡
- 与回执锚点幂等（都走 `on_interrupted`，旧卡已终态 / 新卡已存在时均为 no-op），两次注入同时
  存在不会重复建卡
- 回执文案未被防抖吞掉时，仍由原 REDIRECT 锚点落到新卡通知槽

### 🧾 后台完成回执的合成 id（#43 实测反馈）

后台完成回执进到插件的消息 id 是 `["completion", <task>, <ts>]` 这样的合成序列，不是飞书
`open_message_id`。此前按普通消息建会话后拿它去 reply，必然 `99992354 target_message_gone`，
会话直接标 FAILED：

- `on_message_started` 识别合成 id 形态（`_is_synthetic_turn_id`），按 synthetic 轮次处理：
  卡片直接 `send_card_to_chat`，不再 reply
- 会话键保留原 id，完成回调照常找到会话并封卡

### 🚫 `streamed=False` 重复投递警告（#43 实测反馈）

答案增量被插件接走后，Hermes 自己的 stream consumer 全程空转，网关侧判 `streamed=False` 打
`Normal final-send NOT suppressed` 重复投递警告：

- answer 钩子消费增量时把 `has_durably_delivered_text` 谓词挂到网关 stream consumer 上
  （`mark_stream_delivered`），按 fry 卡片健康状态回答——网关改走抑制分支，警告不再出现
- 卡挂了谓词自然回落 False；封卡重试全失败时 `on_completed_wait` 补标文本回退，最终回复仍由
  网关按纯文本补发，不会丢消息

### 🎨 提示区分区渲染（#43 反馈）

- 通知元素直接渲染 Hermes 原文，去掉「💡 提示」标签（重定向/忙碌文案本身已是完整一句话）
- 展示位分区：忙碌回执 / 重定向这类「本轮进行中」的提示落**卡片底部**（流式态 loading 之前、
  完成态 footer 之前），后台回执这类「完成后」的通知留在**顶部**——不再和工具输出混排
- 重定向收尾旧卡时清掉进行中提示：同一句话不再在新旧两卡各出现一遍

### ⚡ 长任务卡死飞书客户端治理（#46）

50+ 工具步的长任务里，10 Hz × 几十 KB 的整卡重绘把飞书客户端渲染打满直至被系统杀掉（实测
主要发生在安卓端，iOS / 桌面端正常；服务端无法识别渲染端类型，无法按客户端分默认值）。
三处配合治理，≤16KB 场景行为与旧版完全一致：

- **节流暴露为配置**：新增 `streaming.flush_interval_ms`（默认 100 保持打字机手感，收口
  50~2000）。安卓设备为主的部署建议直接设 300~500
- **按载荷自适应降频**（默认开启）：flush 载荷 >16KB 窗口自动放大到 ≥300ms、>40KB ≥500ms，
  卡片拆小后自动回落，永不低于配置基准
- **工具面板只渲染最近 30 步**：header 保留总步数，更早的折叠成一行「…另有 N 步工具调用」，
  估算端同口径截取——整卡 JSON 不再随任务长度无界增长（实测 50 步 ≈ 37KB → 有界），
  同时缓解 300305 与拆卡压力

### ⚠️ 尚未覆盖

- 独立的后台完成通知注入点（`run_notifications` 的 watch 通知路径，目前只在
  `reply_to_message_id` 命中活跃卡时并入）
- `busy_steer_ack_enabled: false` 的实际行为尚未实测

### ✅ 验证

- 全量 **936 passed**，ruff / mypy 相对上一版零新增
- 真实 Hermes **v0.22.0 与 v0.22.5** 源码上的锚点位置回归：`REDIRECT_RESOLVED` 必须落在
  `_steer` 解包与 30s 防抖之间
- 本机不跑依赖 Hermes 源码的 `test_clarify_buttons.py`（CI 照常覆盖）

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

> ⚠️ 本版新增注入点 `REDIRECT_RESOLVED`，`uninstall` + `install` 这一步**必须执行**，
> 不能只更新包——否则连续第二次打断的换卡钩子不会写进 `gateway/run_busy.py`。

### 🔍 升级后自查

| 现象 | 含义 |
| --- | --- |
| `status` 里有 `REDIRECT_RESOLVED` marker | 新注入点已落位 |
| 同一回连续发两条打断消息 | 第二次也出现 `interrupt_abort_old` + `session_created (interrupt redirect)` |
| 后台任务完成 | 卡片正常建立（日志不再出现合成 id 的 `99992354` / `session marked FAILED`） |
| 日志不再出现 `Normal final-send NOT suppressed` | 抑制判定已按卡片状态走通（换为 `Suppressing normal final send ... streamed=True`） |
| 长任务卡片只显示最近 30 步工具、header 步数为总数 | 面板截断生效 |
| 安卓端仍卡顿 | config.yaml 设 `streaming: { flush_interval_ms: 300 }` 后复测；仍有问题请回报卡片大小与开始掉帧的刷新频率 |

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.5.7...v0.5.8](https://github.com/techysy/hermes-fry-cards/compare/v0.5.7...v0.5.8)
