"""codebase 增量分析器（V1.5b）：指纹 diff → 单模块重生成。

契约（01-design §增量机制）：
- 收件箱事件（source=codebase, event_type=module_changed）在 flush 时被消费。
- 指纹 diff：与卡上 metadata.fingerprints 比对，**无变化则跳过**（不浪费 token）。
- protected 卡直接跳过（与增量捆绑发布的不变式，Qoder 教训）。
- 服务端零仓库访问权（D11）：重生成只用事件里带的 snippet，不读磁盘。
- 每次决策写 learning_logs（走收件箱 → 决策级审计），同时写 codebase_runs（run 级汇总）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from gleanmem.config import settings

from ..models.pending_event import PendingEvent
from ..models.wiki_document import WikiDocument
from .analyzer import DESCRIPTION_MAX_CHARS, SYSTEM_PROMPT, _parse_card_json
from .store import card_id

logger = logging.getLogger(__name__)


@dataclass
class IncrementalOutcome:
    """单个 module_changed 事件的处理结果。"""

    event_id: str
    module: str
    action: str  # updated | skipped_unchanged | skipped_protected | deleted | failed
    card_id: str | None = None
    tokens_used: int = 0
    raw_response: str = ""
    error: str | None = None

    @property
    def consumed(self) -> bool:
        """是否可从收件箱移除（failed 保留，交给 P1-1 重试机制）。"""
        return self.action != "failed"


def parse_change_event(event: PendingEvent) -> dict | None:
    try:
        data = json.loads(event.context)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def fingerprints_changed(card: WikiDocument | None, files: list[dict]) -> bool:
    """与卡上固化的指纹比对。卡不存在 → 视为需生成。"""
    if card is None:
        return True
    known = (card.extra_meta or {}).get("fingerprints") or {}
    for f in files:
        path, fp = f.get("path"), f.get("fingerprint")
        if not path:
            continue
        if known.get(path) != fp:
            return True
    # 文件被删除也算变化
    return set(known) != {f.get("path") for f in files if f.get("path")}


class IncrementalDistiller:
    """消费 source=codebase 事件，按模块重生成知识卡。"""

    def __init__(self, session, *, model: str | None = None):
        self._s = session
        self.model = model or settings.llm_model

    async def process(
        self, agent_id: str, events: list[PendingEvent]
    ) -> list[IncrementalOutcome]:
        outcomes: list[IncrementalOutcome] = []
        for event in events:
            outcomes.append(await self._process_one(agent_id, event))
        return outcomes

    async def _process_one(
        self, agent_id: str, event: PendingEvent
    ) -> IncrementalOutcome:
        data = parse_change_event(event)
        if not data or not data.get("module"):
            return IncrementalOutcome(
                event_id=event.id, module="?", action="failed",
                error="unparsable module_changed context",
            )

        module = str(data["module"])
        files = [f for f in (data.get("files") or []) if isinstance(f, dict)]
        commit_sha = data.get("commit_sha")
        cid = card_id(agent_id, module)
        card = await self._s.get(WikiDocument, cid)

        # protected：自动更新绝不覆盖人的修订
        if card is not None and (card.extra_meta or {}).get("protected"):
            return IncrementalOutcome(
                event_id=event.id, module=module,
                action="skipped_protected", card_id=cid,
            )

        # 模块被整体删除 → 软删卡（保留审计痕迹）
        if data.get("change") == "deleted":
            if card is not None:
                card.is_deleted = True
                card.extra_meta = {
                    **(card.extra_meta or {}),
                    "deleted_by_commit": commit_sha,
                }
            return IncrementalOutcome(
                event_id=event.id, module=module, action="deleted", card_id=cid
            )

        # 指纹 diff：无变化则不花 token
        if not fingerprints_changed(card, files):
            return IncrementalOutcome(
                event_id=event.id, module=module,
                action="skipped_unchanged", card_id=cid,
            )

        source_text = self._build_source(files)
        if not source_text.strip():
            # 没有 snippet 可用：客户端未附带内容，无法在服务端重生成
            return IncrementalOutcome(
                event_id=event.id, module=module, action="failed",
                error="no snippet provided; client must resend module content",
            )

        draft, raw, tokens = await self._regenerate(module, source_text, len(files))
        if draft is None:
            return IncrementalOutcome(
                event_id=event.id, module=module, action="failed",
                raw_response=raw, tokens_used=tokens,
                error="LLM regeneration failed or unparsable",
            )

        new_fps = {f["path"]: f.get("fingerprint") for f in files if f.get("path")}
        meta = {
            **(card.extra_meta or {} if card else {}),
            "source": "codebase",
            "module": module,
            "commit_sha": commit_sha,
            "fingerprints": {**((card.extra_meta or {}).get("fingerprints") or {} if card else {}), **new_fps},
            "file_paths": sorted(set(list(new_fps))),
            "narrative": draft["narrative"],
            "wiki_sync_pending": True,  # md 投影待 CLI sync（D11）
        }

        if card is None:
            self._s.add(
                WikiDocument(
                    id=cid, agent_id=agent_id, title=draft["title"],
                    description=draft["description"], content=draft["content"],
                    tags=draft["tags"], extra_meta=meta,
                )
            )
        else:
            card.title = draft["title"]
            card.description = draft["description"]
            card.content = draft["content"]
            card.tags = draft["tags"]
            card.extra_meta = meta
            card.is_deleted = False
        await self._s.flush()

        return IncrementalOutcome(
            event_id=event.id, module=module, action="updated",
            card_id=cid, tokens_used=tokens, raw_response=raw,
        )

    @staticmethod
    def _build_source(files: list[dict]) -> str:
        parts = []
        for f in files:
            snippet = f.get("snippet")
            if snippet:
                parts.append(f"===== {f.get('path')} =====\n{snippet}")
        return "\n\n".join(parts)

    async def _regenerate(
        self, module: str, source_text: str, file_count: int
    ) -> tuple[dict | None, str, int]:
        """重生成单模块知识卡。返回 (draft dict | None, raw, tokens)。"""
        if not settings.llm_api_key:
            logger.warning("No LLM API key; incremental distillation skipped")
            return None, "", 0

        import openai  # late import（同 learning.py）

        client = openai.AsyncOpenAI(
            api_key=settings.llm_api_key, base_url=settings.llm_api_base
        )
        raw, tokens = "", 0
        try:
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"模块路径：{module}\n变更文件数：{file_count}\n"
                            f"（以下为变更后的文件内容片段）\n\n{source_text}"
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content or ""
            if resp.usage:
                tokens = resp.usage.total_tokens or 0
        except Exception:
            logger.exception("Incremental regeneration failed for %s", module)
            return None, raw, tokens

        data = _parse_card_json(raw)
        if not data:
            return None, raw, tokens
        description = (data.get("description") or "").strip()
        if not description:
            # description 是检索命脉，缺失即视为失败
            return None, raw, tokens

        tags = data.get("tags")
        return (
            {
                "title": (data.get("title") or module).strip()[:500],
                "description": description[:DESCRIPTION_MAX_CHARS],
                "content": (data.get("content") or "").strip(),
                "narrative": (data.get("narrative") or "").strip(),
                "tags": [str(t)[:50] for t in (tags if isinstance(tags, list) else [])[:5]],
            },
            raw,
            tokens,
        )
