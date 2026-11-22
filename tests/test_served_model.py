"""路由代理上报的实际服务模型(served_model)——捕获链与渲染(上游 v0.6.7 + v0.6.8 回归修复)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from hermes_fry_cards.cardkit.builder import _model_with_served, _render_footer_field, _render_panel_field
from hermes_fry_cards.patch import ensure_served_model_capture_ctx, install_served_model_body_capture
from hermes_fry_cards.patcher import _answer_hook, _tool_hook


class _FakeCompletions:
    """模拟 OpenAI chat.completions 资源对象(实例可赋值)."""

    def __init__(self, result_fn) -> None:
        self._result_fn = result_fn

    async def create(self, *args, **kwargs):
        return await self._result_fn()


class _FakeChat:
    def __init__(self, completions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions) -> None:
        self.chat = _FakeChat(completions)


class _FakeServedAgent:
    def __init__(self, client=None) -> None:
        self.client = client
        self.last_served_model = None


class _FakeAsyncStream:
    """模拟 OpenAI AsyncStream:__anext__ 产出 chunk,属性可读."""

    def __init__(self, chunks) -> None:
        self._chunks = list(chunks)
        self.response = SimpleNamespace(status_code=200)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _render(name: str, data: dict) -> str | None:
    return _render_footer_field(name, data, is_error=False, is_aborted=False, show_label=False)[0]


def test_model_with_served_rendering() -> None:
    """served_model 与配置名不同才加箭头；相同 / 缺失与旧版逐字一致."""
    assert _model_with_served({"model": "cbcn/hy4-preview", "served_model": "hy4-preview"}).endswith(
        "→ hy4-preview"
    )
    assert _model_with_served({"model": "hy4-preview", "served_model": "hy4-preview"}) == _model_with_served(
        {"model": "hy4-preview"}
    )
    assert _model_with_served({"served_model": "hy4-preview"}) is None
    # footer / panel 两条渲染面都要带箭头,且只有一份「🍟」前缀
    assert "→ hy4-preview" in (_render("model", {"model": "cbcn/hy4-preview", "served_model": "hy4-preview"}) or "")
    panel, _ = _render_panel_field(
        "model",
        {"model": "cbcn/hy4-preview", "served_model": "hy4-preview"},
        reasoning_count=0,
        tool_count=0,
        panel_elapsed_ms=0,
        show_context=False,
        context_mode="text",
    )
    assert panel is not None and panel.count("🍟") == 1 and "→ hy4-preview" in panel


def test_body_capture_wraps_sync_wrapped_create_returning_awaitable() -> None:
    """v0.6.8 回归:OpenAI SDK 的 create 被 required_args 同步包装器包过,
    iscoroutinefunction 为 False,但调用仍返回 awaitable —— 必须照样捕获."""

    def sync_wrapper(*args, **kwargs):  # 模拟 required_args 包装器(同步 def,返回 awaitable)
        return asyncio.sleep(0, result=SimpleNamespace(model="hy4-preview", choices=[1]))

    completions = _FakeCompletions(lambda: asyncio.sleep(0))
    completions.create = sync_wrapper
    agent = _FakeServedAgent(_FakeClient(completions))

    assert install_served_model_body_capture(agent) is True
    assert agent.client.chat.completions._hl_body_model_wrapped is True
    asyncio.run(agent.client.chat.completions.create())
    assert agent.last_served_model == "hy4-preview"

    # 幂等:重复安装不叠加
    install_served_model_body_capture(agent)
    asyncio.run(agent.client.chat.completions.create())
    assert agent.last_served_model == "hy4-preview"


def test_stream_capture_takes_first_chunk_model() -> None:
    """流式:首 chunk 到达即写槽位,流对象其余属性透传."""
    agent = _FakeServedAgent(
        _FakeClient(
            _FakeCompletions(
                lambda: asyncio.sleep(0, result=_FakeAsyncStream([SimpleNamespace(model="hy4-preview")]))
            )
        )
    )
    assert install_served_model_body_capture(agent) is True

    async def _consume() -> None:
        stream = await agent.client.chat.completions.create()
        async for _chunk_obj in stream:
            break
        assert stream.response.status_code == 200

    asyncio.run(_consume())
    assert agent.last_served_model == "hy4-preview"


def test_ensure_served_model_capture_ctx_uses_agent_holder() -> None:
    """ctx.agent_holder 直通本轮 agent:不依赖缓存键(START 处 session_key 未绑定)."""
    completions = _FakeCompletions(lambda: asyncio.sleep(0, result=SimpleNamespace(model="m", choices=[1])))
    agent = _FakeServedAgent(_FakeClient(completions))

    ensure_served_model_capture_ctx(SimpleNamespace(agent_holder=[agent]))
    assert getattr(agent.client.chat.completions, "_hl_body_model_wrapped", False) is True

    # holder 为空 / ctx 缺字段 / None:静默不抛
    ensure_served_model_capture_ctx(SimpleNamespace(agent_holder=[]))
    ensure_served_model_capture_ctx(SimpleNamespace())
    ensure_served_model_capture_ctx(None)


def test_fail_open_without_openai_surface() -> None:
    """anthropic api_mode 等没有 OpenAI 表面:安装器静默返回,不影响任何行为."""
    agent = SimpleNamespace(client=None)
    assert install_served_model_body_capture(agent) is True
    assert install_served_model_body_capture(None) is False


def test_tool_and_answer_hooks_install_body_capture() -> None:
    """tool/answer 钩子注入体经 ctx 兜底安装(多步回合最终响应必被覆盖)."""
    assert "ensure_served_model_capture_ctx" in _tool_hook("    ")
    assert "ensure_served_model_capture_ctx" in _answer_hook("    ")
