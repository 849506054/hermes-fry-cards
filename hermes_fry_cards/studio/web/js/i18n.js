/* fry-cards Studio — 界面 i18n（中 / 英，localStorage 记忆，data-i18n 声明式接入）
 *
 * 字典值为 [zh, en]。接入方式：
 *   data-i18n="key"       → 替换 textContent
 *   data-i18n-html="key"  → 替换 innerHTML（含 <code>/<strong> 的富文本提示用）
 *   data-i18n-title="key" / data-i18n-ph="key" / data-i18n-aria="key"
 * 动态文本（toast / 状态页 / 别名编辑器）用 FryI18n.t("key")。
 */
window.FryI18n = (function () {
  "use strict";

  var LANG_KEY = "fry-lang";
  var DICT = {
    /* ---------- 顶栏 / 导航 ---------- */
    "brand.meta": ["配置 <code>~/.hermes/config.yaml</code>", "Config <code>~/.hermes/config.yaml</code>"],
    "title.lang": ["切换到 English", "切换到中文"],
    "title.theme": ["切换亮 / 暗主题", "Toggle light / dark theme"],
    "tab.config": ["配置", "Config"],
    "tab.preview": ["预览", "Preview"],
    "tab.status": ["状态", "Status"],

    "style.role.header": ["原生标题", "Header"],
    "style.role.body": ["正文与底色", "Body"],
    "style.role.footer": ["底部元数据", "Footer"],
    "style.role.process": ["过程与完成面板", "Process"],
    "style.role.process_title": ["运行中标题", "Process Title"],
    "style.role.reasoning": ["推理正文", "Reasoning"],
    "style.role.reasoning_title": ["推理标题", "Reasoning Title"],
    "style.role.summary": ["摘要", "Summary"],
    "style.role.tool_title": ["工具标题", "Tool Title"],
    "style.role.tool_detail": ["工具详情", "Tool Detail"],
    "style.role.tool_output": ["工具输出", "Tool Output"],
    "style.role.tool_icons": ["工具图标映射", "Tool Icons"],
    "style.role.text_sizes": ["分端字号映射", "Platform text sizes"],
    "style.role.table": ["原生表格", "Table"],
    "style.role.loading": ["加载图", "Loading"],
    "style.role.markdown": ["Markdown 格式", "Markdown"],
    "style.role.clarify": ["交互选择", "Clarify"],
    "style.field.padding": ["内边距", "Padding"],
    "style.field.icon_token": ["标准图标名称", "Icon token"],
    "style.field.icon_color": ["图标颜色", "Icon color"],
    "style.field.icon_img_key": ["图标图片 Key", "Icon img key"],
    "style.field.icon_processing": ["处理中图片 Key", "Processing image key"],
    "style.field.icon_completed": ["完成图片 Key", "Completed image key"],
    "style.field.icon_stopped": ["停止图片 Key", "Stopped image key"],
    "style.field.icon_error": ["出错图片 Key", "Error image key"],
    "style.field.icon_cron": ["定时通知图片 Key", "Cron image key"],
    "style.field.arrow": ["运行中展开箭头", "Running panel arrow"],
    "style.field.completed_arrow": ["封卡统计框展开箭头", "Completed panel arrow"],
    "style.field.text_align": ["文本对齐", "Text align"],
    "style.field.text_color": ["文字颜色", "Text color"],
    "style.field.bold": ["粗体", "Bold"],
    "style.field.italic": ["斜体", "Italic"],
    "style.field.margin": ["外边距", "Margin"],
    "style.field.body_padding": ["正文容器内边距", "Body padding"],
    "style.field.horizontal_spacing": ["水平间距", "Horizontal spacing"],
    "style.field.vertical_spacing": ["垂直间距", "Vertical spacing"],
    "style.field.horizontal_align": ["水平对齐", "Horizontal align"],
    "style.field.vertical_align": ["垂直对齐", "Vertical align"],
    "style.field.surface_padding": ["底色区域内边距", "Surface padding"],
    "style.field.surface_margin": ["底色区域外边距", "Surface margin"],
    "style.field.error_color": ["错误颜色", "Error color"],
    "style.field.separator": ["字段分隔符", "Separator"],
    "style.field.background_color": ["背景色", "Background color"],
    "style.field.border_color": ["边框色", "Border color"],
    "style.field.border_radius": ["边框圆角", "Border radius"],
    "style.field.completed_border": ["完成态边框", "Completed border"],
    "style.field.completed_color": ["完成态颜色", "Completed color"],
    "style.field.stopped_color": ["中止颜色", "Stopped color"],
    "style.field.header_padding": ["面板标题内边距", "Header padding"],
    "style.field.header_background_color": ["面板标题背景色", "Header background color"],
    "style.field.header_vertical_align": ["面板标题垂直对齐", "Header vertical align"],
    "style.field.header_width": ["面板标题宽度", "Header width"],
    "style.field.icon_size": ["图标大小", "Icon size"],
    "style.field.icon_position": ["图标位置", "Icon position"],
    "style.field.icon_expanded_angle": ["展开时图标角度", "Icon expanded angle"],
    "style.field.text_size": ["字号", "Text size"],
    "style.field.running_color": ["运行中颜色", "Running color"],
    "style.field.success_color": ["成功颜色", "Success color"],
    "style.field.unknown_color": ["未知状态颜色", "Unknown color"],
    "style.field.page_size": ["每页行数", "Page size"],
    "style.field.row_height": ["行高", "Row height"],
    "style.field.row_max_height": ["最大行高", "Row max height"],
    "style.field.column_width": ["列宽", "Column width"],
    "style.field.freeze_first_column": ["冻结首列", "Freeze first column"],
    "style.field.header_text_align": ["表头文本对齐", "Header text align"],
    "style.field.header_text_size": ["表头字号", "Header text size"],
    "style.field.header_background_style": ["表头背景样式", "Header background style"],
    "style.field.header_text_color": ["表头文字颜色", "Header text color"],
    "style.field.header_bold": ["表头粗体", "Header bold"],
    "style.field.header_lines": ["表头行数", "Header lines"],
    "style.field.img_key": ["图片 Key", "Img key"],
    "style.field.size": ["图片大小", "Size"],
    "style.field.heading_mode": ["标题级别策略", "Heading mode"],
    "style.field.max_blank_lines": ["最多连续空行", "Max blank lines"],
    "style.field.pending_template": ["待选标题配色", "Pending template"],
    "style.field.completed_template": ["已选标题配色", "Completed template"],
    "style.field.hint_color": ["提示颜色", "Hint color"],
    "style.field.active_hint_color": ["输入中提示颜色", "Active hint color"],
    "style.field.primary_button_type": ["主要按钮样式", "Primary button type"],
    "style.field.secondary_button_type": ["次要按钮样式", "Secondary button type"],
    "style.field.mapping": ["图标映射 (JSON)", "Mapping"],
    "style.field.overrides": ["图标映射 (JSON)", "Overrides"],
    "style.title": ["高级样式 · 按内容区域", "Advanced styles · By content area"],
    "style.help": ["留空沿用既有样式；清空已配置项可恢复默认。保存后下一张新卡采用，已有卡片保留创建时样式。", "Leave blank to inherit the existing style; clear a configured value to restore its default. Saved changes apply to the next new card; existing cards keep their initial style."],
    "style.native-title": ["原生标题字体、字号、字重、颜色和对齐由飞书客户端控制；可配置标题配色、内边距与图标。", "Native title font, size, weight, text color and alignment are client-controlled. Header theme, padding and icon are configurable."],
    "style.inherit": ["沿用默认（留空）", "Inherit default (blank)"],
    "style.platform-sizes": ['JSON 示例：{"reader":{"default":"normal","pc":"normal","mobile":"large"}}。值使用飞书字号枚举；预览优先 pc，其次 default。移动端效果以飞书客户端为准。', 'Example JSON: {"reader":{"default":"normal","pc":"normal","mobile":"large"}}. Use Feishu size enums. Preview uses pc, then default; check mobile appearance in Feishu.'],
    "style.size-reference": ["可选飞书字号枚举，或分端字号映射中已定义的名称。", "Use a Feishu size enum or a name defined in the platform text-size map."],
    "style.error-platform-sizes": ["请输入安全名称到 {default, pc, mobile} 的 JSON 映射，至少设置一端；值使用飞书字号枚举。", "Enter a safe-name JSON map to {default, pc, mobile}, with at least one Feishu size enum per entry."],
    "style.error-map": ["请输入工具名称到图标名称的 JSON 字符串映射。", "Enter a JSON map of tool names to icon token strings."],
    "style.preview-icons": ["预览以名称或占位符表示飞书图标与图片；将鼠标停在占位符上可查看 token 或图片 Key。", "Feishu icons and images use labeled placeholders here; hover to inspect the token or image key."],
    "style.empty-separator": ['输入 "" 使用空分隔符', 'Enter "" for an empty separator'],
    "style.error-range": ["请输入允许范围内的数值。", "Enter a number within the allowed range."],
    "style.error-fields": ["请检查标出的样式字段。", "Check the highlighted style fields."],
    "opt.sc-clarify-input": ["交互选择 · 自定义输入", "Clarify · Custom input"],
    "opt.sc-clarify": ["交互选择（流式中=待选，完成态=已选）", "Clarify (streaming=pending, completed=resolved)"],

    /* ---------- 配置页 ---------- */
    "legend.streaming": ["流式卡片", "Streaming Cards"],
    "lbl.enabled": ["启用流式卡片", "Enable streaming cards"],
    "lbl.width": ["卡片宽度 width_mode", "Card width (width_mode)"],
    "lbl.content-lang": ["内容提示语语言", "Content hint language"],
    "opt.zh": ["zh（中文）", "zh (Chinese)"],
    "opt.en": ["en（English）", "en (English)"],
    "lbl.chat-types": ["发卡片的聊天类型", "Chat types for cards"],
    "opt.all": ["全部类型", "All types"],
    "opt.dm": ["仅私聊 dm", "DM only"],
    "opt.group": ["仅群聊 group", "Group only"],
    "lbl.body-size": ["正文文字大小", "Body text size"],
    "lbl.body-light-color": ["正文浅色底色", "Body background · light theme"],
    "lbl.body-dark-color": ["正文深色底色", "Body background · dark theme"],
    "hint.body-colors": ["按客户端亮 / 暗主题使用对应的六位 HEX 色值，保存后从下一张新卡生效。", "Each client theme uses its six-digit HEX color. Saved colors apply to the next new card."],
    "lbl.card-ttl": ["卡片空闲超时（秒）", "Card idle timeout (seconds)"],
    "hint.card-ttl": ["大于 0、最多 86400 秒；按空闲时长回收会话，保存后下次清理即采用新值。", "More than 0, up to 86400 seconds. Sessions expire by idle time; changes apply at the next prune."],
    "error.card-ttl": ["请输入大于 0、最多 86400 的秒数。", "Enter a number of seconds greater than 0 and at most 86400."],
    "lbl.flush-interval": ["流式刷新节流（毫秒）", "Streaming flush interval (ms)"],
    "hint.flush-interval": ["默认 500ms（50~2000）；兼顾打字机流畅感与移动端防卡顿闪退。", "Default 500ms (50~2000); balances smooth typing effect with crash prevention on mobile."],
    "opt.heading": ["heading（大）", "heading (large)"],
    "opt.notation": ["notation（小）", "notation (small)"],

    "legend.statusbar": ["状态栏 · 顶部 Header 与底部 Footer", "Status Bars · Header & Footer"],
    "sub.header": ["Header 顶部状态栏", "Header (top status bar)"],
    "lbl.header-enabled": ["显示 header", "Show header"],
    "lbl.header-template": ["标题配色", "Header color theme"],
    "opt.template-default": ["默认", "Default"],
    "hint.header-template": ["默认沿用客户端标题样式；选定配色用于新回复卡和定时通知卡。", "Default uses the client header style. The selected theme applies to new reply and cron cards."],
    "lbl.header-min": ["快捷回复去标题阈值（秒，0=始终显示）", "Hide header under duration (s, 0=always show)"],
    "sub.footer": ["Footer 底部元数据栏", "Footer (bottom metadata bar)"],
    "hint.footer": ["ℹ️ 一般不开启：模型 / 耗时 / 上下文等元数据由「统一面板」承载。", "ℹ️ Usually off: model / elapsed / context metadata is carried by the Unified Panel."],
    "lbl.footer-enabled": ["显示 footer", "Show footer"],
    "lbl.footer-label": ["显示字段标签", "Show field labels"],
    "lbl.text-size": ["文字大小", "Text size"],
    "lbl.footer-fields": ["footer 字段", "Footer fields"],
    "tip.fields": ["点按切换显隐（置灰 = 隐藏）；按住拖动调整顺序。", "Click to toggle visibility (grey = hidden); hold and drag to reorder."],
    "fields-note": ["当前为多行字段配置：保存时原样保留，UI 不可编辑。", "Multi-row field config: preserved as-is on save; not editable in the UI."],

    "legend.notice": ["完成通知", "Completion Notice"],
    "lbl.notice-enabled": ["卡片完成后发送提醒消息", "Send a notice when the card completes"],
    "lbl.notice-text": ["提醒文字前缀", "Notice text prefix"],
    "hint.notice": ["通知会回复在卡片消息下，仅在卡片成功收尾后发送；错误会附带「出错」。", "The notice replies under the card message and is only sent after a clean finish; errors are suffixed with “Error”."],

    "legend.panel": ["统一面板（header 字段自由组合）", "Unified Panel (header fields, free combination)"],
    "lbl.panel-expanded": ["完成态面板保持展开", "Keep completed panel expanded"],
    "lbl.process-expanded": ["运行中过程框保持展开", "Keep streaming process frame expanded"],
    "lbl.process-border": ["显示运行中过程框边框", "Show streaming process frame border"],
    "hint.process-border": ["用于下一张新卡的运行中过程框；完成态面板仍使用状态色边框。", "Applies to streaming process frames on the next new card. Completed panels keep their status-colored borders."],
    "lbl.show-reasoning": ["展示推理过程", "Show reasoning"],
    "lbl.show-tool-use": ["展示工具调用面板", "Show tool-use panel"],
    "lbl.show-context": ["面板 header 显示上下文", "Show context in panel header"],
    "lbl.context-mode": ["上下文显示格式", "Context display format"],
    "lbl.max-panels": ["最多独立推理面板数（1~50）", "Max separate reasoning panels (1~50)"],
    "lbl.unified-min": ["面板最小耗时阈值（秒，短回复不显示）", "Min panel duration threshold (s, hidden for short replies)"],
    "lbl.truncate-model": ["面板 header 截断长模型名", "Truncate long model names in panel header"],
    "lbl.panel-fields": ["面板 header 字段", "Panel header fields"],
    "lbl.panel-text-size": ["面板 header 字体", "Panel header text size"],
    "hint.panel-fields": ["ℹ️ 思考数/工具数为面板专属计数；上下文跟随上方「显示上下文」开关与显示格式；无数据的字段自动隐藏。", "ℹ️ Reasoning/tool counts are panel-exclusive; context follows the “show context” switch and format above; fields without data are hidden automatically."],

    "legend.alias": ["模型别名（统一面板 header 模型名）", "Model Aliases (panel header model name)"],
    "lbl.alias-enabled": ["启用别名（关闭则回落 ⇲ 截断）", "Enable aliases (falls back to ⇲ truncation)"],
    "hint.alias": [
      '键 = 模型名子串（不区分大小写，按添加顺序首中即生效）；值填显示名，或点 🕐 加<b>时段人设</b>——按北京时间 HH:MM + 星期自动切换（如 DeepSeek 峰谷：峰段梁文锋⚡️ / 谷段梁文谷⚡️）。改完下一条消息即热生效。',
      "",
    ],
    "btn.add-alias": ["＋ 添加别名", "＋ Add alias"],
    "hint.more": ["ℹ️ 查看说明", "ℹ️ Details"],
    "alias.empty": ["暂无别名——模型名将按 ⇲ 截断显示。", "No aliases — model names will be ⇲-truncated."],
    "ph.alias-key": ["匹配子串，如 deepseek", "Match substring, e.g. deepseek"],
    "ph.alias-name": ["显示名，如 梁文谷⚡️", "Display name, e.g. LiangWengu⚡️"],
    "ph.rule-name": ["该时段显示名", "Display name for this slot"],
    "ph.alias-default": ["默认显示名", "Default display name"],
    "alias.default-row": ["其他时间（以上规则都不命中时）：", "Other times (when no rule above matches):"],
    "preset.work": ["工作日", "Workdays"],
    "preset.weekend": ["周末", "Weekend"],
    "btn.add-rule": ["＋ 时段规则", "＋ Time-slot rule"],
    "title.alias-time": ["时段人设", "Time-slot persona"],
    "title.del": ["删除", "Delete"],
    "confirm.alias-static": ["转回静态名称将丢弃全部时段规则，仅保留默认名。继续？", "Switching back to a static name discards all time-slot rules, keeping only the default name. Continue?"],

    "legend.gsb": ["🛡️ 群聊安全边界（modular Hermes 0.21+）", "🛡️ Group Security Boundary (modular Hermes 0.21+)"],
    "lbl.gsb-enabled": ["启用（群聊回复套输出边界，私聊 DM 不受影响）", "Enable (wraps group replies with an output boundary; DMs unaffected)"],
    "lbl.gsb-allow": ["豁免群白名单（一行一个 chat_id，oc_xxx；填入的群不套边界）", "Exempt group allowlist (one chat_id per line, oc_xxx; listed groups skip the boundary)"],
    "hint.gsb": [
      '开启后群聊回复将被约束：<b>不透露 API key / 密码 / 令牌 / 内网 IP / 凭据 / 私人信息</b>，被群友索要时婉拒并引导私聊。边界走每轮 ephemeral prompt 注入（不碰持久 system prompt），hook 逻辑在插件内，<code>hermes update</code> 不会覆盖；改完下一条消息生效，必要时 <code>hermes gateway restart</code>。豁免群适合多 Agent 协作群。详见 README「群聊安全边界」。',
      "",
    ],

    "btn.reload": ["放弃修改，重新读取", "Discard & reload"],
    "btn.save-preview": ["保存并预览", "Save & preview"],
    "btn.save": ["仅保存", "Save only"],

    /* ---------- 字段 chips ---------- */
    "field.status": ["状态", "Status"],
    "field.elapsed": ["耗时", "Elapsed"],
    "field.speed": ["速度", "Speed"],
    "field.cache": ["缓存命中", "Cache hit"],
    "field.model": ["模型", "Model"],
    "field.tokens": ["Tokens", "Tokens"],
    "field.context": ["上下文", "Context"],
    "field.model_p": ["🍟 模型", "🍟 Model"],
    "field.reasoning_p": ["💭 思考数", "💭 Reasoning"],
    "field.tools_p": ["🔧 工具数", "🔧 Tools"],
    "field.elapsed_p": ["⏱️ 耗时", "⏱️ Elapsed"],
    "field.speed_p": ["⚡ 速度", "⚡ Speed"],

    /* ---------- 预览页 ---------- */
    "hint.preview": [
      '服务端使用<strong>真实 builder</strong> 和当前表单生成卡片 JSON，预览不会保存配置。浏览器近似展示布局、字体、颜色、图标与表格；最终效果以飞书客户端为准。',
      "",
    ],
    "lbl.scenario": ["场景", "Scenario"],
    "opt.sc-workflow": ["工作流交错（思考+工具+答案）", "Interleaved workflow (reasoning+tools+answer)"],
    "opt.sc-short": ["快捷回复（短耗时）", "Quick reply (short)"],
    "opt.sc-tables": ["多表格压力（第6张起压缩）", "Table stress (6th+ compacted)"],
    "opt.sc-longtext": ["超长文本（18KB 截断）", "Extra-long text (18KB clamp)"],
    "lbl.state": ["状态", "State"],
    "opt.completed": ["完成态", "Completed"],
    "opt.streaming": ["流式中", "Streaming"],
    "lbl.outcome": ["结果", "Outcome"],
    "opt.oc-done": ["✅ 完成", "✅ Completed"],
    "opt.oc-error": ["❌ 出错", "❌ Error"],
    "opt.oc-aborted": ["🛑 中止", "🛑 Aborted"],
    "btn.render": ["渲染预览", "Render preview"],
    "preview.empty": ["点击「渲染预览」查看卡片", "Click “Render preview” to see the card"],

    /* ---------- 状态页 ---------- */
    "btn.refresh": ["刷新", "Refresh"],
    "btn.restart": ["重启网关", "Restart gateway"],
    "status.loading": ["加载中…", "Loading…"],
    "confirm.restart": ["重启 Hermes 网关？运行中的会话会被中断。", "Restart the Hermes gateway? Running sessions will be interrupted."],
    "st.patched-gw": ["网关补丁", "Gateway patches"],
    "st.state": ["状态", "State"],
    "st.patches-installed": ["已注入", "Injected"],
    "st.patches-missing": ["未注入", "Not injected"],
    "st.no-hermes": ["未找到 Hermes", "Hermes not found"],
    "st.installed": ["已安装", "Installed"],
    "st.not-installed": ["未安装", "Not installed"],
    "st.unavailable": ["不可用", "Unavailable"],
    "st.verify": ["兼容性（verify）", "Compatibility (verify)"],
    "st.compatible": ["兼容", "Compatible"],
    "st.incompatible": ["不兼容", "Incompatible"],
    "st.runtime": ["运行配置", "Runtime config"],
    "st.streaming": ["流式卡片", "Streaming cards"],
    "st.on": ["已启用", "Enabled"],
    "st.off": ["已停用", "Disabled"],
    "st.content-lang": ["内容提示语", "Content hints"],
    "st.creds": ["飞书凭据", "Feishu credentials"],
    "st.configured": ["已配置", "Configured"],
    "st.missing": ["缺失", "Missing"],
    "st.env": ["Hermes 环境", "Hermes environment"],
    "st.plugin-ver": ["插件版本", "Plugin version"],
    "st.unknown": ["未知", "Unknown"],
    "st.no-cli": ["未找到 CLI", "CLI not found"],
    "st.no-python": ["（未找到 hermes python）", "(hermes python not found)"],
    "st.no-dir": ["（未找到安装目录）", "(install dir not found)"],
    "st.loadfail": ["加载失败：", "Load failed: "],

    /* ---------- toast / 动态 ---------- */
    "toast.badjson": ["响应不是 JSON (HTTP ", "Response is not JSON (HTTP "],
    "toast.reloaded": ["已重新读取磁盘配置", "Config reloaded from disk"],
    "toast.readfail": ["读取失败：", "Read failed: "],
    "toast.saved": ["已保存：", "Saved: "],
    "toast.nochange": ["无配置变化", "No config changes"],
    "toast.savefail": ["保存失败：", "Save failed: "],
    "toast.aliases": ["别名 {} 条", "{} aliases"],
    "toast.previewfail": ["预览失败：", "Preview failed: "],
    "toast.restart-ok": ["网关重启成功", "Gateway restarted"],
    "toast.restart-fail": ["重启失败：", "Restart failed: "],
    "toast.restart-reqfail": ["重启请求失败：", "Restart request failed: "],
    "toast.unknown-err": ["未知错误", "Unknown error"],

    /* ---------- preview.js 渲染占位 ---------- */
    "pv.element": ["元素 ", "Element "],
    "pv.empty": ["(无元素)", "(empty)"],
  };

  // 富文本类 key（取 innerHTML 而非 textContent）
  DICT["hint.warn"] = [
    '⚠️ 保存会<strong>重写整个 config.yaml（YAML 注释会丢失）</strong>；写前自动备份到 <code>~/.hermes/backups/fry_studio/</code>（保留 20 份）。只写白名单键，你手写的其他配置原样保留。样式和初始布局从下一张新卡生效，已有卡片保留创建时样式；display 显示项继续热读。完成通知从下一次完成事件生效，空闲超时从下次清理生效，刷新节流从下次调度生效；启用与聊天类型控制新轮次。凭据和连接设置仍需 <code>hermes gateway restart</code> 生效。',
    '⚠️ Saving <strong>rewrites the whole config.yaml (YAML comments will be lost)</strong>; an automatic backup goes to <code>~/.hermes/backups/fry_studio/</code> (20 kept). Only whitelisted keys are written — your other settings stay intact. Styles and initial layout apply to the next new card; existing cards keep their initial style. Display settings stay live. Notices apply at the next completion, idle timeout at the next prune, and flush interval at the next scheduling. Enabled/chat types control new turns. Credentials and connection settings still require <code>hermes gateway restart</code>.',
  ];
  DICT["hint.alias"][1] =
    'Key = model-name substring (case-insensitive; first match in insertion order wins). Value = display name, or click 🕐 for a <b>time-slot persona</b> — switches automatically by Beijing time HH:MM + weekday (e.g. DeepSeek peak/valley). Hot-applies from the next message.';
  DICT["hint.gsb"][1] =
    'When enabled, group replies are constrained: <b>never reveal API keys / passwords / tokens / internal IPs / credentials / private info</b>, and politely decline + redirect to DM when asked. The boundary rides the per-turn ephemeral prompt (the persistent system prompt is untouched); hook logic lives in the plugin so <code>hermes update</code> won\'t overwrite it. Applies from the next message; run <code>hermes gateway restart</code> if needed. Exempt groups suit multi-agent collaboration. See README "Group Security Boundary".';
  DICT["hint.preview"][1] =
    'The <strong>real builder</strong> generates card JSON from the current form without saving. The browser approximates layout, typography, colors, icons and tables; the Feishu client determines the final appearance.';

  var lang = "zh";
  try { lang = localStorage.getItem(LANG_KEY) || "zh"; } catch (e) { /* 隐私模式忽略 */ }
  // 调试/分享用：?lang=en 或 #lang=en 直接覆盖（不写入 localStorage）
  var m = (location.search + "&" + location.hash.replace(/^#/, "")).match(/lang=(zh|en)/);
  if (m) lang = m[1];

  function t(key) {
    var v = DICT[key];
    if (!v) return key;
    var s = lang === "en" ? v[1] : v[0];
    return s || v[0];
  }

  var HTML_KEYS = {
    "brand.meta": true, "hint.warn": true, "hint.alias": true, "hint.gsb": true, "hint.preview": true,
    "hint.footer": true, "hint.panel-fields": true, "fields-note": true,
  };

  function apply(root) {
    (root || document).querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      if (HTML_KEYS[key]) el.innerHTML = t(key);
      else el.textContent = t(key);
    });
    (root || document).querySelectorAll("[data-i18n-title]").forEach(function (el) {
      el.title = t(el.getAttribute("data-i18n-title"));
    });
    (root || document).querySelectorAll("[data-i18n-ph]").forEach(function (el) {
      el.placeholder = t(el.getAttribute("data-i18n-ph"));
    });
    (root || document).querySelectorAll("[data-i18n-aria]").forEach(function (el) {
      el.setAttribute("aria-label", t(el.getAttribute("data-i18n-aria")));
    });
    document.documentElement.setAttribute("lang", lang === "en" ? "en" : "zh-CN");
    var btn = document.getElementById("lang-toggle");
    if (btn) btn.textContent = lang === "en" ? "中" : "EN";
  }

  function setLang(l) {
    lang = l === "en" ? "en" : "zh";
    try { localStorage.setItem(LANG_KEY, lang); } catch (e) { /* ignore */ }
    apply();
  }

  return { t: t, apply: apply, setLang: setLang, getLang: function () { return lang; } };
})();
