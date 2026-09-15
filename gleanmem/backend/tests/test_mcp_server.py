"""A2 + MCP 身份不变式：SSE 端点对缺失 X-Agent-ID 的请求直接拒绝。

此前实现把 "MISSING" 占位串当 agent_id 用，所有工具调用都会用它写脏数据，
违反「身份不变式」；且每条 SSE 请求都把全部 headers（含密钥）打到 WARNING 日志。
修复后缺失/空 header 在建立 SSE 流之前即返回 401。
"""
from __future__ import annotations

import contextlib
import json

import httpx
from httpx import ASGITransport
from sqlalchemy import select

from gleanmem import main as main_mod
from gleanmem.core.database import async_session_factory
from gleanmem.core.models import PendingEvent
from gleanmem.main import app
from gleanmem.mcp.server import _agent_id, call_tool


def _client(headers: dict | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


async def test_mcp_sse_rejects_missing_agent_id():
    """A2：缺失 X-Agent-ID → 401，绝不建立 SSE 流、不写脏数据。"""
    async with _client() as client:
        r = await client.get("/mcp/sse")
        assert r.status_code == 401
        assert "X-Agent-ID" in r.json()["error"]


async def test_mcp_sse_rejects_blank_agent_id():
    """A2：X-Agent-ID 为空白字符串同样拒绝（防止 '   ' 这类脏值绕过）。"""
    async with _client({"X-Agent-ID": "   "}) as client:
        r = await client.get("/mcp/sse")
        assert r.status_code == 401


# ---------------------------------------------------------------- D2：/health 探活


async def test_health_returns_503_when_db_unreachable(monkeypatch):
    """D2：DB 连不通时 /health 返 503 degraded，避免库挂了探针仍绿。"""

    class _DeadEngine:
        """模拟连不上的 engine：connect() 返回一进去就抛异常的上下文。"""

        def connect(self):
            @contextlib.asynccontextmanager
            async def _cm():
                raise RuntimeError("connection refused")
                yield  # asynccontextmanager 语法需要，不可达

            return _cm()

    monkeypatch.setattr(main_mod, "engine", _DeadEngine())

    async with _client() as client:
        r = await client.get("/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert "connection refused" in body["db"]


# ---------------------------------------------------------------- C2：memorize 工具传参


async def test_memorize_tool_passes_source_session_dedup(space_client):
    """C2：memorize 工具传 source/session_id/dedup_key 时事件带这些字段落库。

    此前 MCP 工具只传 type/context/marked_type，source 永远硬编码 "chat"，
    session_id/dedup_key 无法透传，与 mgr.memorize 全能力脱节。
    """
    _key, agent_id = await space_client()

    token = _agent_id.set(agent_id)
    try:
        result = await call_tool(
            "memorize",
            {
                "type": "agent_mark",
                "context": "测试 C2 传参透传",
                "source": "im",
                "session_id": "sess-123",
                "dedup_key": "dk-456",
            },
        )
    finally:
        _agent_id.reset(token)

    body = json.loads(result[0].text)
    assert body["status"] == "submitted"
    event_id = body["event_id"]

    async with async_session_factory() as s:
        ev = (
            await s.execute(select(PendingEvent).where(PendingEvent.id == event_id))
        ).scalar_one()
        assert ev.agent_id == agent_id
        assert ev.source == "im"
        assert ev.session_id == "sess-123"
        assert ev.dedup_key == "dk-456"


async def test_memorize_tool_defaults_source_to_chat(space_client):
    """C2：不传可选字段时 source 默认 chat、session_id/dedup_key 为 None（向后兼容）。"""
    _key, agent_id = await space_client()

    token = _agent_id.set(agent_id)
    try:
        result = await call_tool(
            "memorize",
            {"type": "user_feedback", "context": "只传必填项"},
        )
    finally:
        _agent_id.reset(token)

    event_id = json.loads(result[0].text)["event_id"]

    async with async_session_factory() as s:
        ev = (
            await s.execute(select(PendingEvent).where(PendingEvent.id == event_id))
        ).scalar_one()
        assert ev.source == "chat"
        assert ev.session_id is None
        assert ev.dedup_key is None
