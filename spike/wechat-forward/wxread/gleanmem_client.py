"""GleanMem REST 客户端：agent 通过真实 HTTP 接入记忆系统.

GleanMemClient：某个 Space 的业务操作（recall / memorize / flush）。
GleanMemAdmin：平台管理（建 / 列 / 归档 Space），用 YDM_ADMIN_KEY。
"""
from __future__ import annotations

import json
import urllib.request


class _Http:
    def __init__(self, base_url: str, key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.key = key

    def _request(self, method: str, path: str, body=None) -> dict:
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
            method=method,
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}


class GleanMemClient(_Http):
    def recall(self, intent: str) -> dict:
        """回忆：返回 {memories: [...]}。"""
        return self._request("POST", "/api/v1/recall", {"intent": intent})

    def memorize(
        self,
        context: str,
        event_type: str = "agent_mark",
        marked_type: str | None = None,
        source: str = "example",
    ) -> dict:
        """提交学习事件进收件箱。"""
        return self._request(
            "POST",
            "/api/v1/learning/events",
            {
                "type": event_type,
                "context": context,
                "marked_type": marked_type,
                "source": source,
            },
        )

    def flush(self) -> dict:
        return self._request("POST", "/api/v1/learning/flush", {})


class GleanMemAdmin(_Http):
    def list_spaces(self) -> list[dict]:
        return self._request("GET", "/api/v1/spaces")

    def create_space(self, name: str, description: str | None = None) -> dict:
        """返回 {agent_id, space_key, ...}，space_key 明文仅此一次。"""
        return self._request(
            "POST",
            "/api/v1/spaces",
            {"name": name, "description": description},
        )

    def archive_space(self, agent_id: str) -> dict:
        return self._request("DELETE", f"/api/v1/spaces/{agent_id}")
