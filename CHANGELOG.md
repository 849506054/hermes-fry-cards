## v0.5.9.1（本地线）
基于上游 v0.5.9 的本地实现，把提示类消息与后台回执的呈现口径对标到 0.5.4.x，并修掉上游这版的几处缺陷。

**提示与后台回执**
- 提示改成单槽位：忙碌回执 / 重定向提示 / 后台回执共用卡片上一个通知元素，后到覆盖先到；
- 提示在流式期间就画到卡上，不再等封卡重绘才出现；
- 封卡时本轮有正文则通知槽里的提示一律不渲染，正文接管那个位置；本轮没有正文（被打断）时保留那一条；
- 后台完成回执并进活跃卡，走同一槽位；无活跃卡时发独立卡，不再退化成纯文本；
- 独立回执卡去掉 header，正文用 `✅ 后台任务已完成 — 命令 (Ns)`，内部注入文案不上屏。

**打断与投递**
- `on_interrupted` 按 `new_message_id` 记 2 秒去重窗口：REDIRECT 与 REDIRECT_RESOLVED 两个注入点各调一次时，不再建出第二个会话；
- 新增可选注入点 `BG_BUSY_SUPPRESS`：插件已接管完成事件时，跳过 Hermes 在 turn_busy 分支补发的纯文本回执；拿不到键时返回 False，照常发。

**测试**
- `tests/conftest.py` 把 `HERMES_HOME` 指到临时目录，避免测试把夹具 id 写进生产活跃卡登记表；
- 全量 992 passed / 1 failed（`test_thinking_tagged_text_does_not_leak_into_answer` 在未改动的 v0.5.9 上就红）/ 4 skipped。

## v0.5.9.4（本地补丁线）
**🐛 本地修复**
- **打断去重键改成 `new_message_id`**：第二个注入点（REDIRECT_RESOLVED）到达时 `old_message_id` 已被换成刚建好的新会话（日志里 old==new），原来按 `(old, new)` 记窗口会漏判，仍会建出第二个会话再 dispose。改成只按 `new_message_id` 记 2 秒窗口。

## v0.5.9.3（本地补丁线）
**🐛 本地修复（未合入上游）**
- **流式期后台回执即时可见**：回执并入活跃卡后只进了 `notices`，卡片上那个提示元素仍停在建卡时的文案，要等封卡整卡重建才出现。flush 新增「通知槽位同步」一步（步骤 1.5）：目标文案与画在元素上的不一致就当场补一次元素更新——本卡有可复用的提示元素（`fry_notice`，未被正文接管）走 `partial_update_element` 原地改文案；没有（本卡从未带提示，或 `fry_notice` 已被正文接管）则在 loading 之前 `add_elements` 补一个流式提示元素（`fry_notice_live_N`，封卡重建时随旧卡消失）。画不上去只记 `notice slot paint failed`，不影响 flush 与主流程。
- **封卡时通知槽提示一律让位正文**：`drop_rendered_progress_notices` 原来只摘「进行中」提示、`kind="background"` 例外；现在只看有没有真画到卡上，画过的那条（含后台回执）一律从完成态正文剔除，位置留给正文——流式时显示，封卡后被正文覆盖。从未落到卡片元素上的提示（例如封卡后才到）仍按原样留在完成态通知位。
- **抑制 turn_busy 的纯文本回执补发**：回执已并进卡片，网关 `_run_process_watcher` 的 `turn_busy` 分支还会 `_send_watcher_message` 补一条 concise 纯文本，用户看到卡外多一条。新增可选注入点 `BG_BUSY_SUPPRESS`，挂在 concise 文案合成之后、`_send_watcher_message` 之前，调用 `hermes_fry_cards.patch.suppress_busy_receipt(...)`：`on_background_completion` 成功接管（并入活跃卡或投出独立静态卡）时按 `session_id` / `session_key` / `started_at` 登记，10 分钟窗口内命中即 `break` 跳过本次发送；拿不到键、登记过期或插件禁用一律返回 False，网关照常发，功能无损。

## v0.5.9.2（本地补丁线）
**🐛 本地修复**
- **同一次打断的重复注入去重**：REDIRECT 与 REDIRECT_RESOLVED 对同一事件各调一次 `on_interrupted`，两次的 `old_message_id` 可能都指向刚建好的新会话（同 id），原幂等判据分不出来，于是又建一个会话、提示写进随后被丢弃的那个。按 `(old, new)` 记 2 秒窗口，重复调用直接返回并记 `interrupt_duplicate_skipped`。

