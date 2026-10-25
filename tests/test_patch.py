"""patch.py 测试 — 注入 hook 入口的参数修正."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import hermes_fry_cards.patch as patch_module
from hermes_fry_cards.patch import on_cron_deliver


class TestCronDeliver:
    """#21: cron 卡片头部时间必须是投递时刻，注入点传入的 next_run_at 一律覆盖."""

    def _capture(self) -> tuple[dict, MagicMock]:
        captured: dict = {}
        ctrl = MagicMock()
        ctrl.enabled = True
        ctrl.on_cron_deliver.side_effect = lambda **kwargs: captured.update(kwargs) or True
        return captured, ctrl

    def test_overrides_future_next_run_at(self) -> None:
        captured, ctrl = self._capture()
        with patch.object(patch_module, "get_controller", return_value=ctrl):
            assert on_cron_deliver(
                chat_id="oc_x", content="done", run_time="2099-01-01T00:00:00+08:00"
            ) is True
        run_time = datetime.fromisoformat(captured["run_time"])
        assert abs((datetime.now().astimezone() - run_time).total_seconds()) < 120

    def test_overrides_past_and_empty_values(self) -> None:
        for incoming in ("2020-01-01T00:00:00+08:00", ""):
            captured, ctrl = self._capture()
            with patch.object(patch_module, "get_controller", return_value=ctrl):
                assert on_cron_deliver(chat_id="oc_x", content="done", run_time=incoming) is True
            run_time = datetime.fromisoformat(captured["run_time"])
            assert abs((datetime.now().astimezone() - run_time).total_seconds()) < 120


class TestSuppressBusyReceipt:
    """注入点：回执已由卡片投递 → 跳过网关 turn_busy 的纯文本补发."""

    def setup_method(self) -> None:
        from hermes_fry_cards.controller import clear_merged_busy_receipts

        clear_merged_busy_receipts()

    def teardown_method(self) -> None:
        from hermes_fry_cards.controller import clear_merged_busy_receipts

        clear_merged_busy_receipts()

    def _ctrl(self, *, enabled: bool = True) -> MagicMock:
        ctrl = MagicMock()
        ctrl.enabled = enabled
        return ctrl

    def test_hit_returns_true(self) -> None:
        from hermes_fry_cards.controller import remember_merged_busy_receipt
        from hermes_fry_cards.patch import suppress_busy_receipt

        remember_merged_busy_receipt(session_id="proc_hit")
        with patch.object(patch_module, "get_controller", return_value=self._ctrl()):
            assert suppress_busy_receipt(
                session_id="proc_hit", platform_name="feishu", chat_id="oc_x"
            ) is True

    def test_miss_returns_false(self) -> None:
        from hermes_fry_cards.patch import suppress_busy_receipt

        with patch.object(patch_module, "get_controller", return_value=self._ctrl()):
            assert suppress_busy_receipt(
                session_id="proc_miss", platform_name="feishu", chat_id="oc_x"
            ) is False

    def test_missing_keys_return_false(self) -> None:
        """注入块拿不到任何键时不能吞掉网关的回执."""
        from hermes_fry_cards.patch import suppress_busy_receipt

        with patch.object(patch_module, "get_controller", return_value=self._ctrl()):
            assert suppress_busy_receipt(platform_name="feishu", chat_id="oc_x") is False

    def test_non_feishu_platform_returns_false(self) -> None:
        from hermes_fry_cards.controller import remember_merged_busy_receipt
        from hermes_fry_cards.patch import suppress_busy_receipt

        remember_merged_busy_receipt(session_id="proc_tg")
        with patch.object(patch_module, "get_controller", return_value=self._ctrl()):
            assert suppress_busy_receipt(
                session_id="proc_tg", platform_name="telegram", chat_id="1"
            ) is False

    def test_disabled_plugin_returns_false(self) -> None:
        from hermes_fry_cards.controller import remember_merged_busy_receipt
        from hermes_fry_cards.patch import suppress_busy_receipt

        remember_merged_busy_receipt(session_id="proc_off")
        with patch.object(patch_module, "get_controller", return_value=self._ctrl(enabled=False)):
            assert suppress_busy_receipt(
                session_id="proc_off", platform_name="feishu", chat_id="oc_x"
            ) is False

    def test_controller_error_is_swallowed(self) -> None:
        """插件侧异常不能影响网关主流程."""
        from hermes_fry_cards.patch import suppress_busy_receipt

        with patch.object(
            patch_module, "get_controller", side_effect=RuntimeError("boom")
        ):
            assert suppress_busy_receipt(
                session_id="proc_err", platform_name="feishu", chat_id="oc_x"
            ) is False
