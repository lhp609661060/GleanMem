"""yd-memory-service entry point.

FastAPI app that mounts:
- REST API  (/api/v1/…)
- MCP SSE   (/mcp/sse, /mcp/messages/)

Start:  uv run yd-memory  or  uvicorn yd_memory_service.main:app
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from yd_memory_service.api.auth import router as auth_router
from yd_memory_service.api.codebase import router as codebase_router
from yd_memory_service.api.learning import router as learning_router
from yd_memory_service.api.memories import router as memories_router
from yd_memory_service.api.observations import router as observations_router
from yd_memory_service.api.recall import router as recall_router
from yd_memory_service.api.spaces import router as spaces_router
from yd_memory_service.config import settings
from yd_memory_service.core.database import engine
from yd_memory_service.core.metrics import metrics
from yd_memory_service.mcp.server import mcp_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="yd-memory-service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST
app.include_router(auth_router)
app.include_router(spaces_router)
app.include_router(learning_router)
app.include_router(observations_router)
app.include_router(recall_router)
app.include_router(memories_router)
app.include_router(codebase_router)

# MCP SSE — mount the MCP Starlette app under FastAPI
app.mount("/mcp", mcp_app)


@app.get("/health")
async def health():
    """探活 DB：库连通返 200，否则 503，避免库挂了探针仍绿。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": str(exc)[:200]},
        )
    return {"status": "ok", "db": "ok"}


@app.get("/metrics")
async def metrics_endpoint():
    """轻量运行时指标：flush/recall 调用次数与耗时、LLM token 消耗。

    进程内计数器，无 Prometheus 重依赖。适合单实例可观测；多实例需升级 exporter。
    """
    return metrics.snapshot()


def main() -> None:
    """Console entry point (uv run yd-memory)."""
    import uvicorn

    uvicorn.run(
        "yd_memory_service.main:app",
        host=settings.host,
        port=settings.port,
    )
