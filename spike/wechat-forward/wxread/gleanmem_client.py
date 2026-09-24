"""GleanMem REST 客户端：agent 通过真实 HTTP 接入记忆系统."""
from __future__ import annotations

import json
import urllib.request


class GleanMemClient:
    def __init__(self, base_url: str, space_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.key = space_key

    def _post(self, path: str, body: dict | list) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def recall(self, intent: str) -> dict:
        """回忆：返回 {items: [...]}。"""
        return self._post("/api/v1/recall", {"intent": intent})

    def memorize(
        self,
        context: str,
        event_type: str = "agent_mark",
        marked_type: str | None = None,
        source: str = "example",
    ) -> dict:
        """提交学习事件进收件箱。"""
        return self._post(
            "/api/v1/learning/events",
            {
                "type": event_type,
                "context": context,
                "marked_type": marked_type,
                "source": source,
            },
        )

    def flush(self) -> dict:
        return self._post("/api/v1/learning/flush", {})
