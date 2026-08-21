"""MCP stdio 接入入口：给 DSH 等本地 MCP client 使用。

身份来源：环境变量 ``YDM_AGENT_ID``（由 DSH 插件配置注入），
与 SSE 的 ``X-Agent-ID`` header 解析到同一个 agent_id 概念。

用法：
    python yd_memory_service/mcp/stdio_server.py
"""
from __future__ import annotations

import asyncio
import os
import sys

# 允许直接以脚本方式运行（不依赖包安装）
_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from mcp.server.stdio import stdio_server  # noqa: E402

from yd_memory_service.mcp.server import _agent_id, mcp_server  # noqa: E402


async def run() -> None:
    agent_id = os.environ.get("YDM_AGENT_ID", "").strip()
    if not agent_id:
        raise SystemExit("YDM_AGENT_ID env var is required")

    _agent_id.set(agent_id)
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
