"""D20：杂项加固 —— chat 事件 dedup 幂等 + CORS 来源可配。

身份强度（admin 密钥比较、MCP 鉴权）与 api_key_hash 唯一索引分别由
test_admin.py / test_mcp_server.py / 迁移覆盖，这里只钉 REST 事件幂等与 CORS 接线。
"""
from __future__ import annotations

import httpx
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport

from gleanmem.config import Settings, settings
from gleanmem.main import app


def _client(key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    )


async def test_dedup_key_makes_resubmit_idempotent(space_client):
    key, _aid = await space_client()
    body = {"type": "user_feedback", "context": "厦门客户使用顺丰快递", "dedup_key": "chat-msg-42"}
    async with _client(key) as c:
        first = (await c.post("/api/v1/learning/events", json=body)).json()
        second = (await c.post("/api/v1/learning/events", json=body)).json()

    assert first["status"] == "submitted"
    assert second["status"] == "duplicate"
    assert second["event_id"] == first["event_id"], "重试不该产生第二条事件，也不该换 id"


async def test_events_without_dedup_key_always_append(space_client):
    """dedup_key 为空不参与唯一约束：同样内容两次提交仍是两条事件。"""
    key, _aid = await space_client()
    body = {"type": "user_feedback", "context": "完全一样的内容"}
    async with _client(key) as c:
        ids = [(await c.post("/api/v1/learning/events", json=body)).json()["event_id"] for _ in range(2)]

    assert len(set(ids)) == 2


# ---------------------------------------------------------------- CORS


def test_cors_origins_parsed_from_env_value():
    assert Settings(cors_origins="https://a.com, https://b.com ,").cors_origin_list() == [
        "https://a.com",
        "https://b.com",
    ]
    assert Settings(cors_origins="*").cors_origin_list() == ["*"]


def test_app_cors_middleware_reads_settings():
    """接线校验：allow_origins 来自配置而非硬编码 —— 改 YDM_CORS_ORIGINS 才有效。"""
    mw = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert mw.kwargs["allow_origins"] == settings.cors_origin_list()
    assert Settings.model_fields["cors_origins"].default == "*"  # 默认维持旧行为
