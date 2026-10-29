## 🍟 hermes-fry-cards v0.6.0

> 长任务渲染减压与封卡可靠性专项：流式 reasoning 不再每秒全文重发 28KB 打满飞书客户端，
> 长工具轮次封卡不再必撞 300305、降级替换不再因序号重号失效；附带 #51 死锁自愈、
> 节流默认值调优与面板字号可配。

### 🧊 reasoning 全文钳制 + 元素级降频（#52）

`stream_element` 是覆盖语义全文重发，reasoning 分支此前没有任何长度上限。生产实测
单次推送峰值 28.8KB、单轮累计 1.3MB，客户端每秒重绘 20KB+ markdown 直至掉帧卡顿
（#46 的载荷分档管不到这一半：16–40KB 档等于不降频，28.8KB 也永远进不了降频档）：

- **尾部窗口钳制**：流式 reasoning 与完成态统一面板的 reasoning 全文统一钳到
  `_REASONING_MAX_BYTES`（10KB）——头部摘要 + 尾部最新结论 + 中间省略标记，
  两处观感一致；短思考（p50 ≈ 2.8KB）完全不受影响
- **元素级降频**：`adapt_payload_size` 新增单元素分档——单元素全文 >8KB → ≥1s、
  >16KB → ≥2s，按「最大单元素」判定而非 flush 累加和（累加和会把多个小元素叠成
  大档位判错）；元素档位优先于 #46 的载荷档位，重绘量大头直接被拉开窗口

### 📦 封卡 300305 三连修（#53）

工具面板步骤数超过二十来步的长轮次，封卡整卡重建时把面板子元素连同流式期已提交的
一份重复计数，服务端按「已有 + 本次」累计越过 200 硬上限 → 必撞 300305；降级替换
又复用了删 loading 已占用的序号回 300317，降级卡自己也换不上，卡片停在半路：

- **紧凑面板**：完成态统一面板内的工具组每步只渲染标题行（工具名 · 状态），不渲染
  入参与输出，子元素数压回 1/3（30 步 ≈ 31 格）；封卡后无人回看过程细节，header
  仍保留总步数与总耗时
- **应急剥离**：封卡仍撞 300305 时按 `unified_panel` id 剥离统一面板重发一次，
  正文与状态优先入卡；仍超限才走整卡降级
- **序号修复**：降级替换跳到 `sequence += 2`，不再复用删 loading 占用的 N+1，
  降级卡能真正换上

### 🔁 300301 Duplicate ID 死锁自愈（#51）

飞书偶发瞬态异常重试后，元素在云端实际已落地但客户端记 `created=False`，下轮重发
`add_elements` 被 300301 拒绝并陷入死锁假死。`_do_batch_update` 现在识别 300301
并自愈：纠正段状态、补齐计数，下轮 flush 自动转回正常流式更新。另适配 Hermes
0.22.5+1713 的 `run_notifications.py` wrapper 布局。

### ⚙️ 其他

- 流式刷新节流默认值 100ms → 500ms（2 Hz，兼顾打字机流畅感与移动端防闪退），
  Studio 新增「流式刷新节流（毫秒）」设置项（50–2000ms）
- 统一面板 header 字体可配：`display.panel_text_size`（默认 `notation`），Studio
  可视化设置全链路接通
- 全量 **1035 passed**，ruff / mypy 相对 v0.5.10 零新增

### 📦 升级

本版**无新增注入点**，只升级包 + 重启网关即可；重跑 `uninstall + install` 亦无副作用。
从 v0.5.9 及以下升级、或 Hermes 升到 0.22.5+ 的部署，仍建议完整重跑一遍：

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
| 跑一轮长思考任务 | 日志 `CardKit stream element ... type=reasoning len=` 单次 ≤10000，不再出现 28KB 级全文推送 |
| 工具调用密集的长轮次封卡 | 不再出现 `complete attempt N failed: code=300305`；真撞上时日志有 `complete retry without unified panel`，卡片正文与状态仍正常落卡 |
| 封卡连续失败降级 | 卡片能替换为红色降级提示卡，日志不再出现 `300317 sequence number compare failed` |
| 移动端长任务 | 客户端不再因 reasoning 全文重绘掉帧卡顿 |

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.5.10...v0.6.0](https://github.com/techysy/hermes-fry-cards/compare/v0.5.10...v0.6.0)
