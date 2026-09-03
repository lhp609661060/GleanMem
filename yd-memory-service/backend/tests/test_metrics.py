"""D4：运行时指标（flush/recall 耗时、LLM token）计数与 /metrics 端点。

进程内计数器，无 Prometheus 重依赖。覆盖：计数/耗时累计、avg 派生、
空状态零值不除零、timed_flush/recall 上下文管理器上报、/metrics 端点结构。
"""
from __future__ import annotations

import httpx
from httpx import ASGITransport

from yd_memory_service.core.metrics import Metrics, metrics, timed_flush, timed_recall
from yd_memory_service.main import app


def test_metrics_counter_observes_flush_recall():
    """D4：observe_flush/recall 计 count + 累计耗时，snapshot 派生 avg。"""
    m = Metrics()
    m.observe_flush(10.0)
    m.observe_flush(30.0)
    m.observe_recall(5.0)
    snap = m.snapshot()
    assert snap["flush"]["count"] == 2
    assert snap["flush"]["total_ms"] == 40.0
    assert snap["flush"]["avg_ms"] == 20.0
    assert snap["recall"]["count"] == 1
    assert snap["recall"]["avg_ms"] == 5.0


def test_metrics_token_usage_accumulates():
    """D4：LLM token 累计 prompt/completion/total。"""
    m = Metrics()
    m.observe_llm_tokens(100, 50)
    m.observe_llm_tokens(200, 30)
    snap = m.snapshot()["llm_tokens"]
    assert snap["prompt_tokens"] == 300
    assert snap["completion_tokens"] == 80
    assert snap["total_tokens"] == 380


def test_metrics_snapshot_empty_state_no_divzero():
    """D4：未上报时 snapshot 返零值（count=0, avg=0），不抛除零。"""
    m = Metrics()
    snap = m.snapshot()
    assert snap["flush"] == {"count": 0, "total_ms": 0.0, "avg_ms": 0.0}
    assert snap["recall"] == {"count": 0, "total_ms": 0.0, "avg_ms": 0.0}
    assert snap["llm_tokens"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_timed_flush_increments_global_counter():
    """D4：timed_flush 上下文管理器上报到全局单例（埋点用的原语）。"""
    before = metrics.snapshot()["flush"]["count"]
    with timed_flush():
        pass
    assert metrics.snapshot()["flush"]["count"] == before + 1


def test_timed_recall_increments_global_counter():
    """D4：timed_recall 上下文管理器上报到全局单例（埋点用的原语）。"""
    before = metrics.snapshot()["recall"]["count"]
    with timed_recall():
        pass
    assert metrics.snapshot()["recall"]["count"] == before + 1


def test_timed_flush_records_even_on_exception():
    """D4：with 块内抛异常时仍上报耗时（except 路径不漏埋点）。"""
    before = metrics.snapshot()["flush"]["count"]
    try:
        with timed_flush():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert metrics.snapshot()["flush"]["count"] == before + 1


async def test_metrics_endpoint_returns_snapshot_structure():
    """D4：GET /metrics 返 200 + flush/recall/llm_tokens 三段结构。"""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/metrics")
        assert r.status_code == 200
        body = r.json()
        assert "flush" in body
        assert "recall" in body
        assert "llm_tokens" in body
        assert "count" in body["flush"]
        assert "avg_ms" in body["flush"]
        assert "total_tokens" in body["llm_tokens"]
