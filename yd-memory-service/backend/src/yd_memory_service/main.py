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

from yd_memory_service.api.auth import router as auth_router
from yd_memory_service.api.codebase import router as codebase_router
from yd_memory_service.api.learning import router as learning_router
from yd_memory_service.api.memories import router as memories_router
from yd_memory_service.api.observations import router as observations_router
from yd_memory_service.api.recall import router as recall_router
from yd_memory_service.api.spaces import router as spaces_router
from yd_memory_service.config import settings
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
    return {"status": "ok"}


def main() -> None:
    """Console entry point (uv run yd-memory)."""
    import uvicorn

    uvicorn.run(
        "yd_memory_service.main:app",
        host=settings.host,
        port=settings.port,
    )
