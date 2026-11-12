"""Decode-speed reading: fry-metrics samples → footer speed field.

The plugin file lives outside this repo (``/opt/data/plugins/fry-metrics``), so the
test loads it by path, aliases it as ``fry_metrics`` and then checks that
``collect_stream_stats`` prefers the pure-decode figure over the end-to-end one.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from hermes_fry_cards import patch

PLUGIN_PATH = Path("/opt/data/plugins/fry-metrics/__init__.py")


def _load_metrics():
    if not PLUGIN_PATH.is_file():
        pytest.skip("fry-metrics plugin not installed")
    spec = importlib.util.spec_from_file_location("fry_metrics", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fry_metrics"] = module
    spec.loader.exec_module(module)
    return module


class _Agent:
    session_id = "sess-1"
    session_cache_read_tokens = 0
    # end-to-end history: 2 calls of 4s latency each, 100 output tokens each → 25 tok/s
    _api_latency_history: ClassVar[list[float]] = [4.0, 4.0]
    _api_output_history: ClassVar[list[float]] = [100.0, 100.0]


class _Gateway:
    def __init__(self, agent):
        self._running_agents = {"sk": agent}


def test_summary_excludes_first_token_wait():
    metrics = _load_metrics()
    metrics.clear()
    # call 1: start 100.0, first chunk 101.0 (TTFT 1s), end 103.0 → 100 tok over 2s
    metrics.on_post_api_request(
        session_id="sess-1", started_at=100.0, ended_at=103.0, first_chunk_at=101.0,
        usage={"output_tokens": 100},
    )
    # call 2: start 200.0, first chunk 200.5 (TTFT .5s), end 202.5 → 100 tok over 2s
    metrics.on_post_api_request(
        session_id="sess-1", started_at=200.0, ended_at=202.5, first_chunk_at=200.5,
        usage={"output_tokens": 100},
    )
    got = metrics.summary("sess-1")
    assert got["decode_tokens_per_sec"] == pytest.approx(50.0)
    assert got["ttft_ms"] == pytest.approx(750.0)
    assert got["calls"] == 2


def test_samples_without_first_chunk_are_skipped():
    metrics = _load_metrics()
    metrics.clear()
    metrics.on_post_api_request(
        session_id="s", started_at=1.0, ended_at=5.0, first_chunk_at=None,
        usage={"output_tokens": 80},
    )
    assert metrics.summary("s") == {}


def test_collect_stream_stats_prefers_decode_speed():
    metrics = _load_metrics()
    metrics.clear()
    metrics.on_post_api_request(
        session_id="sess-1", started_at=10.0, ended_at=14.0, first_chunk_at=10.5,
        usage={"output_tokens": 350},
    )
    stats = patch.collect_stream_stats(_Gateway(_Agent()), "sk")
    # 350 tokens over 3.5s decode = 100 tok/s, not the 25 tok/s end-to-end figure
    assert stats["tokens_per_sec"] == pytest.approx(100.0)
    assert stats["decode_tokens_per_sec"] == pytest.approx(100.0)
    assert stats["ttft_ms"] == pytest.approx(500.0)


def test_collect_stream_stats_falls_back_without_samples():
    metrics = _load_metrics()
    metrics.clear()
    stats = patch.collect_stream_stats(_Gateway(_Agent()), "sk")
    assert "decode_tokens_per_sec" not in stats
    assert stats["tokens_per_sec"] == pytest.approx(25.0)
