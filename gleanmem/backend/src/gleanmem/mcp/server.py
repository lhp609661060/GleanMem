"""MCP SSE Server — 3 tools exposed to Dify."""

from __future__ import annotations

import contextvars
import json
import logging
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

from gleanmem.api.deps import resolve_space_key
from gleanmem.config import settings
from gleanmem.core.database import async_session_factory
from gleanmem.core.manager import MemoryManager
from gleanmem.orchestrator.recall import recall

from .tools import TOOL_DEFINITIONS

logger = logging.getLogger("ydm.mcp")

# Per-request agent_id，由建连时的 space_key 解析结果固化（见 handle_sse）
_agent_id: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_agent_id", default="")


mcp_server: Server = Server("gleanmem")


@mcp_server.list_tools()
async def list_tools() -> list:
    return [types.Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"])
            for t in TOOL_DEFINITIONS]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list:
    aid = _agent_id.get()
    if not aid:
        return [types.TextContent(type="text", text=json.dumps(
            {"error": "身份未解析（缺少有效 space_key）"}, ensure_ascii=False))]

    async with async_session_factory() as session:
        mgr = MemoryManager(session)

        try:
            if name == "recall":
                result = await recall(arguments.get("intent", ""), aid)
                return [types.TextContent(type="text", text=json.dumps(result.to_dict(), ensure_ascii=False, indent=2))]

            elif name == "load_memory":
                # 归属校验：只能加载本 Space 的记忆（修复 v2 越权缺陷）
                mem = await mgr.memories.get_scoped(arguments.get("id", ""), aid)
                text = json.dumps({"id": mem.id, "title": mem.title, "type": mem.type, "content": mem.content, "weight": round(mem.weight, 2)}, ensure_ascii=False, indent=2) if mem else json.dumps({"error": "记忆不存在或不属于当前 Space"}, ensure_ascii=False)
                return [types.TextContent(type="text", text=text)]

            elif name == "memorize":
                event = await mgr.memorize(
                    agent_id=aid,
                    event_type=arguments.get("type", "agent_mark"),
                    context=arguments.get("context", ""),
                    marked_type=arguments.get("marked_type"),
                    source=arguments.get("source", "chat"),
                    session_id=arguments.get("session_id"),
                    dedup_key=arguments.get("dedup_key"),
                )
                await session.commit()
                return [types.TextContent(type="text", text=json.dumps({"status": "submitted", "event_id": event.id, "message": "已提交，对话结束后统一分析处理"}, ensure_ascii=False))]

            else:
                return [types.TextContent(type="text", text=json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False))]
        except Exception as exc:
            import traceback
            # 细节只进日志；回显 str(exc) 会把内部信息（SQL/路径/依赖报错）交给客户端
            logger.error("Tool %s failed: %s\n%s", name, exc, traceback.format_exc())
            await session.rollback()
            return [types.TextContent(type="text", text=json.dumps(
                {"error": "internal error"}, ensure_ascii=False))]


# --- Starlette wiring --------------------------------------------------

sse_transport = SseServerTransport("/messages/")


def _json_error(status: int, message: str) -> Response:
    return Response(
        content=json.dumps({"error": message}, ensure_ascii=False),
        media_type="application/json",
        status_code=status,
    )


async def resolve_mcp_identity(request: Request) -> tuple[str | None, tuple[int, str] | None]:
    """建连前解析身份，返回 (agent_id, error)。error = (status, message)。

    默认（mcp_auth_required=true）要求 `X-Space-Key: <space_key>`，与 REST 同一套
    哈希查库强度；`X-Agent-ID` 降级为一致性校验项（带且不匹配 → 403）。
    `YDM_MCP_AUTH_REQUIRED=false` 时回退旧的「仅信 X-Agent-ID」约定，只供内网演示。
    """
    legacy_aid = request.headers.get("X-Agent-ID", "").strip()
    space_key = request.headers.get("X-Space-Key", "").strip().removeprefix("Bearer ").strip()

    if not settings.mcp_auth_required:
        if not legacy_aid:
            return None, (401, "X-Agent-ID header 未设置")
        return legacy_aid, None

    if not space_key:
        logger.warning("MCP SSE rejected: missing X-Space-Key")
        return None, (401, "缺少 X-Space-Key header")

    async with async_session_factory() as session:
        aid = await resolve_space_key(space_key, session)
    if not aid:
        logger.warning("MCP SSE rejected: invalid or archived key (prefix=%s)", space_key[:8])
        return None, (401, "space_key 无效或已归档")
    if legacy_aid and legacy_aid != aid:
        logger.warning("MCP SSE rejected: X-Agent-ID mismatch")
        return None, (403, "X-Agent-ID 与 space_key 不一致")
    return aid, None


async def handle_sse(request: Request) -> Response:
    # 身份不变式：解析不出合法身份就拒绝建立 SSE，绝不用占位串写脏数据。
    aid, error = await resolve_mcp_identity(request)
    if error:
        return _json_error(*error)
    _agent_id.set(aid)
    logger.debug("MCP SSE connected agent_id=%s", aid)
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as (read_stream, write_stream):
        await mcp_server.run(
            read_stream, write_stream, mcp_server.create_initialization_options())
    return Response()


mcp_app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ],
)
