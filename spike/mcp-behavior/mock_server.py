"""P0-2: Mock recall/load_memory/memorize server for Agent behavior verification.

In-memory dict storage. No database. Purpose: test whether Dify Agent
actively calls recall and memorize as expected by the V1 design.

Run:
    uv run python mock_server.py  (or use the mcp-echo venv)

Listens on 0.0.0.0:8765. MCP URL for Dify:
    http://10.60.80.165:8765/mcp/sse
"""

from __future__ import annotations

import contextvars
import json
import uuid
from typing import Any

import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

# --- in-memory "database" ---

_memories: dict[str, dict] = {}  # mem_id -> {title, content, weight, type}
_wiki: dict[str, str] = {}       # slug -> content
_call_log: list[dict] = []       # every tool call logged for analysis


def _agent_id(request: Request) -> str:
    """Extract agent_id from X-Agent-ID header (P0-1 verified path)."""
    return request.headers.get("X-Agent-ID", "default")


_last_sse_request: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_last_sse_request", default=None
)


def _snapshot(request: Request) -> dict[str, Any]:
    return {
        "agent_id": _agent_id(request),
        "headers": {k: v for k, v in request.headers.items()},
    }


# --- MCP server + 3 tools ------------------------------------------

server: Server = Server("gleanmem-mock")


# Seed some test memories so recall returns something useful.
_test_memories = [
    ("发货单重量单位", "统一使用吨(t),不使用千克(kg)"),
    ("厦门客户快递规则", "厦门地区客户发货统一使用顺丰快递"),
    ("异常处理经验", "发货单确认异常需要双人复核"),
    ("日期格式规则", "所有日期使用 yyyy-MM-dd 格式"),
]
for i, (title, content) in enumerate(_test_memories):
    _memories[f"mem_{i}"] = {
        "title": title,
        "content": content,
        "weight": 0.8 + i * 0.02,
        "type": "reference",
    }


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="recall",
            description=(
                "检索与当前任务相关的历史记忆和业务规则。"
                "在回答用户问题或处理用户任务之前调用此工具，获取可能相关的经验、规则和偏好。"
                "intent 参数用一句话描述你当前需要什么信息，不需要构造关键词。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "用自然语言描述当前需要什么信息",
                    }
                },
                "required": ["intent"],
            },
        ),
        types.Tool(
            name="load_memory",
            description="加载指定记忆的完整内容。当 recall 返回的摘要不够详细时使用。",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "记忆的 id",
                    }
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="memorize",
            description=(
                "提交值得长期记住的信息。"
                "使用场景：1) 用户明确告知新规则或纠正错误时"
                " 2) 你自己判断这条信息有长期价值。"
                "注意：提交后不会立即生效，对话结束后统一处理。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["user_feedback", "agent_mark"],
                        "description": "user_feedback=用户显式告知/纠正; agent_mark=Agent 自己判断有价值",
                    },
                    "context": {
                        "type": "string",
                        "description": "完整上下文描述，越具体越好",
                    },
                },
                "required": ["type", "context"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    snapshot = _last_sse_request.get() or {}
    aid = snapshot.get("agent_id", "default")

    if name == "recall":
        intent = arguments.get("intent", "")
        # "Search" by simple matching on intent keywords against memory title+content.
        matched = []
        keywords = set(intent.lower().split())
        for mid, mem in _memories.items():
            text = (mem["title"] + " " + mem["content"]).lower()
            score = sum(1 for kw in keywords if kw in text) + mem["weight"] * 0.5
            if score > 0:
                matched.append({**mem, "id": mid, "score": round(score, 2)})
        matched.sort(key=lambda x: x["score"], reverse=True)

        result = {
            "memories": matched[:5],
            "wiki_refs": [
                {"id": slug, "title": slug, "description": content[:80]}
                for slug, content in _wiki.items()
            ],
            "hint": f"找到 {len(matched)} 条相关记忆",
        }
        text = json.dumps(result, ensure_ascii=False, indent=2)

    elif name == "load_memory":
        mid = arguments.get("id", "")
        mem = _memories.get(mid)
        if mem:
            text = json.dumps({"id": mid, **mem}, ensure_ascii=False, indent=2)
        else:
            text = json.dumps({"error": f"记忆 {mid} 不存在"}, ensure_ascii=False)

    elif name == "memorize":
        event = {
            "type": arguments.get("type"),
            "context": arguments.get("context"),
            "agent_id": aid,
        }
        text = json.dumps(
            {"status": "submitted", "message": "已提交，对话结束后将统一分析处理"},
            ensure_ascii=False,
        )

    else:
        text = json.dumps({"error": f"unknown tool: {name}"})

    _call_log.append({
        "agent_id": aid,
        "tool": name,
        "args": arguments,
        "timestamp": "manual",
    })

    return [types.TextContent(type="text", text=text)]


# --- Starlette wiring -----------------------------------------------

sse_transport = SseServerTransport("/mcp/messages/")


async def handle_sse(request: Request) -> Response:
    _last_sse_request.set(_snapshot(request))
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
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
