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

from yd_memory_service.core.database import async_session_factory
from yd_memory_service.core.manager import MemoryManager
from yd_memory_service.orchestrator.recall import recall

from .tools import TOOL_DEFINITIONS

# Per-request agent_id from X-Agent-ID header
_agent_id: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_agent_id", default="")


mcp_server: Server = Server("yd-memory-service")


@mcp_server.list_tools()
async def list_tools() -> list:
    return [types.Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"])
            for t in TOOL_DEFINITIONS]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list:
    aid = _agent_id.get()
    if not aid:
        return [types.TextContent(type="text", text=json.dumps(
            {"error": "X-Agent-ID header 未设置"}, ensure_ascii=False))]

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
            logging.getLogger("ydm.mcp").error("Tool %s failed: %s\n%s", name, exc, traceback.format_exc())
            await session.rollback()
            return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]


# --- Starlette wiring --------------------------------------------------

sse_transport = SseServerTransport("/messages/")


async def handle_sse(request: Request) -> Response:
    # 身份不变式：缺失 X-Agent-ID 直接拒绝建立 SSE，绝不用占位串写脏数据。
    aid = request.headers.get("X-Agent-ID", "").strip()
    if not aid:
        logging.getLogger("ydm.mcp").warning("MCP SSE rejected: missing X-Agent-ID")
        return Response(
            content=json.dumps(
                {"error": "X-Agent-ID header 未设置"}, ensure_ascii=False
            ),
            media_type="application/json",
            status_code=401,
        )
    _agent_id.set(aid)
    logging.getLogger("ydm.mcp").debug("MCP SSE connected agent_id=%s", aid)
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
