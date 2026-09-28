"""MCP 端身份：D16 起 SSE 必须带 X-Space-Key（与 REST 同一套强度）。

历史缺陷链：v2 用 "MISSING" 占位串 → A2 修成缺 X-Agent-ID 即 401；但 X-Agent-ID
是可伪造的明文身份，知道任意 agent_id 即可读写该 Space（07 评审 R6）。
D16 把身份来源改为 space_key 哈希查库，X-Agent-ID 降级为一致性校验；
YDM_MCP_AUTH_REQUIRED=false 保留旧约定作为内网演示逃生口。
"""
from __future__ import annotations

import contextlib
import json

import httpx
from httpx import ASGITransport
from sqlalchemy import select, update
from starlette.requests import Request

from gleanmem import main as main_mod
from gleanmem.config import settings
from gleanmem.core.database import async_session_factory
from gleanmem.core.models import AgentSpace, PendingEvent
from gleanmem.main import app
from gleanmem.mcp import server as server_mod
from gleanmem.mcp.server import _agent_id, call_tool, resolve_mcp_identity


def _client(headers: dict | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


def _request(headers: dict | None = None) -> Request:
    """构造只带 headers 的最小 Request——身份解析不碰 SSE 流，无需起连接。"""
    raw = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    return Request(
        {"type": "http", "method": "GET", "path": "/mcp/sse", "headers": raw, "query_string": b""}
    )


async def test_mcp_sse_rejects_missing_space_key():
    """D16：无 X-Space-Key → 401，绝不建立 SSE 流、不写脏数据。"""
    async with _client() as client:
        r = await client.get("/mcp/sse")
        assert r.status_code == 401
        assert "X-Space-Key" in r.json()["error"]


async def test_mcp_sse_rejects_blank_space_key():
    """空白 key 同样拒绝（防止 '   ' 这类脏值绕过）。"""
    aid, error = await resolve_mcp_identity(_request({"X-Space-Key": "   "}))
    assert aid is None and error[0] == 401


async def test_resolve_identity_accepts_valid_key(space_client):
    key, agent_id = await space_client()
    aid, error = await resolve_mcp_identity(_request({"X-Space-Key": key}))
    assert error is None
    assert aid == agent_id


async def test_resolve_identity_bears_prefix_key(space_client):
    """Dify 侧若配成 `Bearer ydm_xxx`，剥前缀后仍有效。"""
    key, agent_id = await space_client()
    aid, error = await resolve_mcp_identity(_request({"X-Space-Key": f"Bearer {key}"}))
    assert error is None and aid == agent_id


async def test_resolve_identity_rejects_unknown_key():
    aid, error = await resolve_mcp_identity(_request({"X-Space-Key": "ydm_not_a_real_key"}))
    assert aid is None and error[0] == 401


async def test_resolve_identity_rejects_archived_key(space_client):
    """归档 = 停用：与 REST 侧同语义（resolve_space_key 单一实现保证）。"""
    key, agent_id = await space_client()
    async with async_session_factory() as s:
        await s.execute(update(AgentSpace).where(AgentSpace.agent_id == agent_id).values(status="archived"))
        await s.commit()
    aid, error = await resolve_mcp_identity(_request({"X-Space-Key": key}))
    assert aid is None and error[0] == 401


async def test_resolve_identity_rejects_agent_id_mismatch(space_client):
    """key 有效但 X-Agent-ID 指向别的 Space → 403，不接受越权身份。"""
    key, _ = await space_client()
    _other_key, other_aid = await space_client()
    aid, error = await resolve_mcp_identity(
        _request({"X-Space-Key": key, "X-Agent-ID": other_aid})
    )
    assert aid is None and error[0] == 403


async def test_resolve_identity_allows_consistent_agent_id_header(space_client):
    key, agent_id = await space_client()
    aid, error = await resolve_mcp_identity(
        _request({"X-Space-Key": key, "X-Agent-ID": agent_id})
    )
    assert error is None and aid == agent_id


async def test_resolve_identity_legacy_mode_falls_back_to_header(space_client, monkeypatch):
    """逃生口：mcp_auth_required=false 时回退旧的「仅信 X-Agent-ID」行为。"""
    monkeypatch.setattr(settings, "mcp_auth_required", False)
    _key, agent_id = await space_client()
    aid, error = await resolve_mcp_identity(_request({"X-Agent-ID": agent_id}))
    assert error is None and aid == agent_id

    aid, error = await resolve_mcp_identity(_request())
    assert aid is None and error[0] == 401


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


# ------------------------------------------------- D20：工具异常不回显内部信息


async def test_call_tool_hides_internal_error_details(space_client, monkeypatch):
    """异常细节只进日志；回显 str(exc) 会把 SQL/路径等内部信息交给客户端。"""
    _key, agent_id = await space_client()

    async def _boom(*_a, **_kw):
        raise RuntimeError("asyncpg: relation long_term_memories ... secret")

    monkeypatch.setattr(server_mod, "recall", _boom)

    token = _agent_id.set(agent_id)
    try:
        result = await call_tool("recall", {"intent": "任意"})
    finally:
        _agent_id.reset(token)

    body = json.loads(result[0].text)
    assert body["error"] == "internal error"
    assert "secret" not in result[0].text


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
