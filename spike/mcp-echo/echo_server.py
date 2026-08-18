"""Minimum MCP SSE echo server for Dify integration spike (P0-1).

Purpose: verify that Dify 1.13.3 can register a Python MCP SSE server,
discover its `echo` tool, and call it from an Agent node — including
whether Dify allows configuring per-App MCP URLs (agent_id-per-URL
scheme) and whether it forwards conversation context we could use
instead of URL-embedded agent_id.

Run:
    uv run python echo_server.py

The server listens on 0.0.0.0:8765 with two endpoints:
    GET  /mcp/sse         SSE stream (register this in Dify)
    POST /mcp/messages/   MCP message channel

Dify runs in Docker; on macOS reach the host via
`host.docker.internal:8765` when configuring the MCP Server URL in Dify.
Port 8765 was chosen to sidestep the yd-agent-fresh generator that
occupies 8000 in this workstation.
"""

from __future__ import annotations

import contextvars
import json
from typing import Any

import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

# --- request-scoped snapshot the tool handler can read -----------------
# Dify's tool call travels: GET /mcp/sse (establishes SSE) -> POST
# /mcp/messages/?session_id=... (tool invocation). By the time our tool
# handler runs it no longer has direct access to the HTTP request, so we
# stash the last SSE request context in a contextvar and echo it back.
_last_sse_request: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_last_sse_request", default=None
)


def _snapshot_request(request: Request) -> dict[str, Any]:
    return {
        "path": request.url.path,
        "query_string": request.url.query,
        "query_params": dict(request.query_params),
        "headers": {
            k: v
            for k, v in request.headers.items()
            if k.lower()
            not in {
                "host",
                "connection",
                "accept",
                "accept-encoding",
                "cache-control",
                "cookie",
                "authorization",
            }
        },
        "client": f"{request.client.host}:{request.client.port}" if request.client else None,
    }


# --- MCP server + echo tool --------------------------------------------

server: Server = Server("mcp-echo-spike")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="echo",
            description=(
                "Spike echo tool. Returns the input message plus a snapshot "
                "of what the MCP server saw for this connection (URL query, "
                "headers) — used to verify Dify's MCP integration behaviour."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "任意字符串,原样回显",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "测试预填参数——在 Dify 里设成固定值,看是否透传",
                    },
                },
                "required": ["message"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name != "echo":
        raise ValueError(f"unknown tool: {name}")

    snapshot = _last_sse_request.get() or {}

    # Flat text so Dify Agent shows everything inline.
    lines = [
        f"ECHO: {arguments.get('message')}",
        f"AGENT_ID(from args): {arguments.get('agent_id', '(not passed)')}",
        f"QUERY_PARAMS: {json.dumps(snapshot.get('query_params', {}), ensure_ascii=False)}",
        f"HEADERS: {json.dumps(snapshot.get('headers', {}), ensure_ascii=False)}",
        f"CLIENT: {snapshot.get('client', 'unknown')}",
    ]
    return [types.TextContent(type="text", text="\n".join(lines))]


# --- Starlette wiring --------------------------------------------------

sse_transport = SseServerTransport("/mcp/messages/")


async def handle_sse(request: Request) -> Response:
    _last_sse_request.set(_snapshot_request(request))
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
    # connect_sse yields until the client disconnects; nothing to return.
    return Response()


app = Starlette(
    debug=True,
    routes=[
        Route("/mcp/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/mcp/messages/", app=sse_transport.handle_post_message),
    ],
)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
