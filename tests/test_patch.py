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
