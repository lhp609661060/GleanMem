"""LearningModel — deferred async learning pipeline.

Three modes:
- llm:        LLM analyses pending events, returns decisions as JSON.
- heuristic:  Rule-based: every memorize → store; dedupe by title_key.
- direct:     memorize → store directly (Agent already judged).

Flush runs under PG advisory lock keyed by agent_id.
Every decision writes a learning_logs row (including raw LLM output).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gleanmem.config import settings

from .metrics import metrics
from .long_term.pg_store import LongTermStore
from .models.learning_log import LearningLog
from .models.long_term_memory import LongTermMemory
from .models.pending_event import PendingEvent
from .models.wiki_document import WikiDocument
from .types import DecisionAction, EventType, LearningMode, MemoryType
from .wiki.db_store import WikiStore

logger = logging.getLogger(__name__)


@dataclass
class MemoryDecision:
    action: DecisionAction
    event_id: str
    title: str = ""
    content: str = ""
    memory_type: MemoryType = MemoryType.REFERENCE
    merge_with_id: str | None = None
    tags: list[str] | None = None
    metadata: dict | None = None
    target: str = "memory"  # memory | wiki（观察蒸馏产物映射）
    description: str = ""  # target=wiki 必填，≤100 字
    evidence: list[str] | None = None  # 溯源 event 引用（防幻觉：观察归纳必须非空）
    raw_response: str | None = None  # LLM 原始输出（审计落库）


class LearningModel:
    def __init__(self, session: AsyncSession):
        self._s = session
        self._store = LongTermStore(session)
        self._wiki = WikiStore(session)

    @staticmethod
    def _title_key(title: str) -> str:
        """Normalise a title for dedupe comparison."""
        return re.sub(r"\s+", "", title.lower())

    # ------------------------------------------------------------------

    async def run_pipeline(
        self,
        agent_id: str,
        learning_mode: str,
        decay_per_day: float = 0.95,
        min_weight: float = 0.1,
        max_memories: int = 0,
    ) -> list[MemoryDecision] | None:
        """Flush pipeline: acquire lock → fetch events → analyse → commit → log.

        Returns None when another flush for the same agent_id holds the lock
        (caller should surface this as a "skipped" status).
        max_memories <= 0 视为不限制；> 0 时 flush 末尾软删溢出条数。
        """

        # 1. Acquire advisory lock (non-blocking)
        lock_key = f"flush_{agent_id}"
        result = await self._s.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:key))"),
            {"key": lock_key},
        )
        if not result.scalar():
            logger.info("Flush for %s already running, skip", agent_id)
            return None

        try:
            return await self._run(
                agent_id, learning_mode, decay_per_day, min_weight, max_memories
            )
        finally:
            await self._s.execute(
                text("SELECT pg_advisory_unlock(hashtext(:key))"),
                {"key": lock_key},
            )

    async def _run(
        self,
        agent_id: str,
        learning_mode: str,
        decay_per_day: float,
        min_weight: float,
        max_memories: int = 0,
    ) -> list[MemoryDecision]:
        # 2. SELECT … FOR UPDATE
        stmt = (
            select(PendingEvent)
            .where(PendingEvent.agent_id == agent_id)
            .order_by(PendingEvent.created_at)
            .with_for_update()
        )
        result = await self._s.execute(stmt)
        events = result.scalars().all()

        if not events:
            return []

        # 3-5. 按 source 分派分析并处理
        # 注意：分派必须**白名单**式（== "chat"/"example"），不能写 != "observation"——
        # 否则 V1.5 的 source=codebase 事件会误入 chat 分支被当聊天素材学习。
        chat_events = [e for e in events if e.source in ("chat", "example")]
        obs_events = [e for e in events if e.source == "observation"]
        codebase_events = [e for e in events if e.source == "codebase"]
        processed: list[MemoryDecision] = []
        chat_raw: str | None = None
        obs_raw: str | None = None

        # chat 分支：决策与事件 1:1 绑定
        if chat_events:
            if learning_mode == LearningMode.DIRECT:
                ds = self._direct_analyze(chat_events)
            elif learning_mode == LearningMode.LLM:
                ds, chat_raw = await self._llm_analyze(chat_events)
            else:  # heuristic
                ds = await self._heuristic_analyze(chat_events, agent_id)

            if not ds:
                # P1-1：无决策时事件不得删除
                await self._handle_undecided(chat_events, agent_id, chat_raw, learning_mode)
            else:
                evt_map = {e.id: e.event_type for e in chat_events}
                decided_ids: set[str] = set()
                for d in ds:
                    await self._commit_and_log(
                        d, agent_id, evt_map.get(d.event_id, ""),
                        chat_events[0].session_id, learning_mode,
                        d.raw_response or chat_raw, "chat",
                    )
                    # 只删除成功提交的：FAILED（含 commit 失败）必须保留走 P1-1 重试，
                    # 否则 Python 层异常会丢事件（PG 层异常由整事务 rollback 兜底侥幸安全）。
                    if d.event_id and d.action != DecisionAction.FAILED:
                        decided_ids.add(d.event_id)
                if decided_ids:
                    await self._s.execute(
                        delete(PendingEvent).where(PendingEvent.id.in_(decided_ids))
                    )
                undecided = [e for e in chat_events if e.id not in decided_ids]
                if undecided:
                    await self._handle_undecided(undecided, agent_id, chat_raw, learning_mode)
            processed += ds

        # observation 分支：归纳决策与事件批绑定（evidence 引用），成功即整批删除
        if obs_events:
            if learning_mode == LearningMode.LLM:
                ds, obs_raw = await self._observation_analyze_llm(obs_events)
            else:
                ds = await self._observation_analyze_rule(obs_events)

            if not ds:
                await self._handle_undecided(obs_events, agent_id, obs_raw, learning_mode)
            else:
                for d in ds:
                    await self._commit_and_log(
                        d, agent_id, "observation", obs_events[0].session_id,
                        learning_mode, d.raw_response or obs_raw, "observation",
                    )
                # 整批绑定语义：任一 commit 失败则整批保留走 P1-1 重试，不删除事件
                if any(d.action == DecisionAction.FAILED for d in ds):
                    await self._handle_undecided(obs_events, agent_id, obs_raw, learning_mode)
                else:
                    obs_ids = [e.id for e in obs_events]
                    await self._s.execute(
                        delete(PendingEvent).where(PendingEvent.id.in_(obs_ids))
                    )
            processed += ds

        # codebase 分支（V1.5b）：指纹 diff → 单模块重生成，产物是 wiki 知识卡
        if codebase_events:
            processed += await self._codebase_incremental(agent_id, codebase_events, learning_mode)

        # 6. Decay weights（读 Space 配置，不再硬编码）
        await self._store.decay_weights(agent_id, decay_per_day, min_weight)

        # 7. 上限治理：超出 max_memories 时软删最低权重记忆（配置项此前未接通）
        if max_memories > 0:
            archived = await self._store.archive_overflow(agent_id, max_memories)
            if archived:
                logger.info(
                    "Space %s memory overflow: archived %d (limit=%d)",
                    agent_id, archived, max_memories,
                )

        return processed

    async def _codebase_incremental(
        self,
        agent_id: str,
        events: Sequence[PendingEvent],
        learning_mode: str,
    ) -> list[MemoryDecision]:
        """消费 source=codebase 事件（V1.5b）。

        与 chat/observation 分支的差异：
        - 产物固定是 wiki 知识卡（不是 memory），由 IncrementalDistiller 直接写库；
        - 每个事件独立成败：updated/skipped/deleted 消费掉，failed 走 P1-1 重试；
        - 同时写 run 级审计（codebase_runs, mode=incremental），保持审计链不断。
        """
        from .codebase.incremental import IncrementalDistiller
        from .codebase.store import CodebaseStore

        distiller = IncrementalDistiller(self._s)
        outcomes = await distiller.process(agent_id, list(events))

        store = CodebaseStore(self._s)
        run = await store.start_run(
            agent_id=agent_id,
            mode="incremental",
            repo_path="(flush)",
            commit_sha=next(
                (
                    (e.extra_meta or {}).get("commit_sha")
                    for e in events
                    if (e.extra_meta or {}).get("commit_sha")
                ),
                None,
            ),
            model=distiller.model,
        )

        decisions: list[MemoryDecision] = []
        consumed_ids: list[str] = []
        by_id = {e.id: e for e in events}

        for out in outcomes:
            action = (
                DecisionAction.STORE if out.action == "updated"
                else DecisionAction.FAILED if out.action == "failed"
                else DecisionAction.DISCARD
            )
            event = by_id.get(out.event_id)
            self._s.add(
                LearningLog(
                    agent_id=agent_id,
                    session_id=event.session_id if event else None,
                    source="codebase",
                    event_type=f"module_changed:{out.action}",
                    event_context=f"module={out.module} card={out.card_id or '-'}",
                    decision_action=action.value,
                    decision_target="wiki",
                    analyzer_mode=learning_mode,
                    llm_raw_response=out.raw_response or None,
                    error_message=out.error,
                )
            )
            run.tokens_used += out.tokens_used
            if out.action == "updated":
                run.cards_written += 1
            elif out.action == "skipped_protected":
                run.cards_skipped_protected += 1

            if out.consumed:
                consumed_ids.append(out.event_id)
            decisions.append(
                MemoryDecision(
                    action=action,
                    event_id=out.event_id,
                    title=out.module,
                    content=out.card_id or "",
                    target="wiki",
                )
            )

        if consumed_ids:
            await self._s.execute(
                delete(PendingEvent).where(PendingEvent.id.in_(consumed_ids))
            )
        # failed 事件保留 + retry_count 递增（P1-1 复用）
        failed = [by_id[o.event_id] for o in outcomes if not o.consumed and o.event_id in by_id]
        if failed:
            await self._handle_undecided(failed, agent_id, None, learning_mode)

        await store.finish_run(
            run,
            status="succeeded" if any(o.consumed for o in outcomes) or not outcomes else "failed",
            error_message=next((o.error for o in outcomes if o.error), None),
        )
        return decisions

    # -- analysers -------------------------------------------------------

    def _direct_analyze(self, events: Sequence[PendingEvent]) -> list[MemoryDecision]:
        return [
            MemoryDecision(
                action=DecisionAction.STORE,
                event_id=e.id,
                title=e.context[:200],
                content=e.context,
                memory_type=MemoryType.FEEDBACK
                if e.event_type == EventType.USER_FEEDBACK
                else MemoryType.REFERENCE,
            )
            for e in events
        ]

    async def _heuristic_analyze(
        self, events: Sequence[PendingEvent], agent_id: str
    ) -> list[MemoryDecision]:
        decisions: list[MemoryDecision] = []
        for e in events:
            if e.event_type == EventType.USER_FEEDBACK:
                decisions.append(
                    MemoryDecision(
                        action=DecisionAction.STORE,
                        event_id=e.id,
                        title=e.context[:200],
                        content=e.context,
                        memory_type=MemoryType.FEEDBACK,
                    )
                )
            else:
                decisions.append(
                    MemoryDecision(
                        action=DecisionAction.STORE,
                        event_id=e.id,
                        title=e.context[:200],
                        content=e.context,
                        memory_type=MemoryType.REFERENCE,
                    )
                )
        return decisions

    async def _llm_analyze(
        self, events: Sequence[PendingEvent]
    ) -> tuple[list[MemoryDecision], str]:
        """Call LLM to classify events.

        Returns (decisions, raw_response)。失败或空结果返回 ([], raw)，
        由 _run 的 P1-1 重试机制处理——绝不静默丢弃事件。
        """
        if not settings.llm_api_key:
            logger.warning("No LLM API key configured; events will be retried")
            return [], ""

        import openai  # late import

        client = openai.AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
        )

        events_text = "\n".join(
            f"[{i}] type={e.event_type} | {e.context[:300]}"
            for i, e in enumerate(events)
        )

        raw = ""
        try:
            resp = await client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a memory analysis assistant. For each event, "
                            "decide: store (long-term memory), discard (not useful), "
                            "or merge (similar to existing). Return JSON array. "
                            "Each item MUST include event_index (the [N] prefix of "
                            "the event it analyzes) so it can be bound back reliably:\n"
                            '[{"event_index":0,"action":"store|discard|merge","title":"...","content":"...","memory_type":"reference|feedback|project|user"}]'
                        ),
                    },
                    {"role": "user", "content": events_text},
                ],
                temperature=0.1,
                max_tokens=1000,
            )
            raw = resp.choices[0].message.content or ""
            # D4：上报 LLM token 消耗（部分 provider 不返 usage，缺失则记 0）
            usage = getattr(resp, "usage", None)
            if usage is not None:
                metrics.observe_llm_tokens(
                    getattr(usage, "prompt_tokens", 0) or 0,
                    getattr(usage, "completion_tokens", 0) or 0,
                )
        except Exception:
            logger.exception("LLM analysis failed; keeping events for retry")
            return [], raw

        parsed = self._parse_llm_json(raw)
        decisions: list[MemoryDecision] = []
        for i, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            try:
                action = DecisionAction(item.get("action", "store"))
            except ValueError:
                action = DecisionAction.STORE
            try:
                memory_type = MemoryType(item.get("memory_type", "reference"))
            except ValueError:
                memory_type = MemoryType.REFERENCE
            # E2：优先按 LLM 返回的 event_index 绑定事件，避免数组下标错位
            # （LLM 漏一个/多一个/重排时，enumerate 下标会绑错事件）。
            # LLM 未返回 event_index 时 fallback 到 enumerate 下标（维持现状，不退化）。
            idx = item.get("event_index")
            if isinstance(idx, int) and 0 <= idx < len(events):
                event = events[idx]
            elif i < len(events):
                event = events[i]
            else:
                event = None
            decisions.append(
                MemoryDecision(
                    action=action,
                    event_id=event.id if event else "",
                    title=item.get("title") or (event.context[:200] if event else ""),
                    content=item.get("content") or (event.context if event else ""),
                    memory_type=memory_type,
                    raw_response=raw,
                )
            )
        return decisions, raw

    @staticmethod
    def _parse_llm_json(raw: str) -> list:
        """Strip ```json fences and parse; non-list → []."""
        text = raw.strip()
        if not text:
            return []
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        try:
            parsed = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM JSON 解析失败，按 P1-1 返回空决策：%s", exc)
            return []
        return parsed if isinstance(parsed, list) else []

    # -- observation analysers（契约见 01-design §c 观察分析器契约）--------

    async def _observation_analyze_rule(
        self, events: Sequence[PendingEvent]
    ) -> list[MemoryDecision]:
        """heuristic/direct：观察事件不做归纳，直接存 reference 记忆并固化溯源。"""
        decisions: list[MemoryDecision] = []
        for e in events:
            try:
                payload = json.loads(e.context)
            except Exception:
                payload = {}
            entity = payload.get("entity", "unknown")
            evidence = [e.dedup_key] if e.dedup_key else []
            decisions.append(
                MemoryDecision(
                    action=DecisionAction.STORE,
                    event_id=e.id,
                    title=f"{entity} {e.event_type}"[:200],
                    content=e.context,
                    memory_type=MemoryType.REFERENCE,
                    metadata={
                        "source": "observation",
                        "origin": entity,
                        "evidence": evidence,
                    },
                )
            )
        return decisions

    async def _observation_analyze_llm(
        self, events: Sequence[PendingEvent]
    ) -> tuple[list[MemoryDecision], str]:
        """llm：按冻结契约归纳业务规律；无引用即拒绝；失败交 P1-1 重试。"""
        if not settings.llm_api_key:
            logger.warning("No LLM API key configured; observation events will be retried")
            return [], ""

        import openai  # late import

        client = openai.AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
        )

        events_payload = []
        for e in events:
            try:
                events_payload.append(json.loads(e.context))
            except Exception:
                events_payload.append({"raw": e.context})
        events_text = json.dumps(events_payload, ensure_ascii=False)

        raw = ""
        try:
            resp = await client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是业务观察分析助手。输入是业务系统推来的观察事件列表。"
                            "归纳稳定可复用的业务规则：1) 单事件即明确规则（如状态流转定义）直接 store；"
                            "2) 多条同实体事件呈现稳定模式 → 归纳成一条；3) 一次性噪音事件 → discard。"
                            "返回 JSON 数组：[{\"action\":\"store|discard\",\"title\":\"≤40字\","
                            "\"content\":\"完整规则描述\",\"memory_type\":\"reference|feedback\","
                            "\"target\":\"memory|wiki\",\"description\":\"target=wiki 时必填 ≤100字\","
                            "\"evidence\":[\"event_id\",...]}]"
                        ),
                    },
                    {"role": "user", "content": events_text},
                ],
                temperature=0.1,
                max_tokens=1000,
            )
            raw = resp.choices[0].message.content or ""
            # D4：上报 LLM token 消耗（部分 provider 不返 usage，缺失则记 0）
            usage = getattr(resp, "usage", None)
            if usage is not None:
                metrics.observe_llm_tokens(
                    getattr(usage, "prompt_tokens", 0) or 0,
                    getattr(usage, "completion_tokens", 0) or 0,
                )
        except Exception:
            logger.exception("Observation LLM analysis failed; keeping events for retry")
            return [], raw

        parsed = self._parse_llm_json(raw)
        decisions: list[MemoryDecision] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            try:
                action = DecisionAction(item.get("action", "store"))
            except ValueError:
                action = DecisionAction.STORE
            try:
                memory_type = MemoryType(item.get("memory_type", "reference"))
            except ValueError:
                memory_type = MemoryType.REFERENCE
            evidence = item.get("evidence") or []
            if action == DecisionAction.STORE and not evidence:
                # 防幻觉：无引用即拒绝（同 pattern 纪律）
                action = DecisionAction.DISCARD
            decisions.append(
                MemoryDecision(
                    action=action,
                    event_id="",  # 归纳决策与事件批绑定，由 _run 整批删除
                    title=(item.get("title") or "")[:200],
                    content=item.get("content") or "",
                    memory_type=memory_type,
                    target=item.get("target", "memory"),
                    description=(item.get("description") or "")[:100],
                    evidence=evidence,
                    raw_response=raw,
                )
            )
        return decisions, raw

    async def _handle_undecided(
        self,
        events: Sequence[PendingEvent],
        agent_id: str,
        raw: str | None,
        learning_mode: str,
    ) -> None:
        """P1-1：无决策的事件保留并累加 retry_count；≥3 次写 failed 日志后移除。"""
        for e in events:
            e.retry_count = (e.retry_count or 0) + 1
            if e.retry_count >= 3:
                self._s.add(
                    LearningLog(
                        agent_id=agent_id,
                        session_id=e.session_id,
                        source=e.source,
                        event_type=e.event_type,
                        event_context=e.context[:500],
                        decision_action=DecisionAction.FAILED.value,
                        decision_target="long_term",
                        analyzer_mode=learning_mode,
                        error_message="analysis returned no decision after retries",
                        llm_raw_response=raw,
                    )
                )
                await self._s.delete(e)

    # -- commit + log ----------------------------------------------------

    async def _commit_and_log(
        self,
        d: MemoryDecision,
        agent_id: str,
        event_type: str,
        session_id: str | None,
        learning_mode: str,
        llm_raw_response: str | None = None,
        source: str = "chat",
    ) -> None:
        memory_id: str | None = None
        error_msg: str | None = None
        decision_target = "long_term"

        try:
            if d.action == DecisionAction.STORE:
                if d.target == "wiki":
                    # 观察蒸馏 → wiki 产物（Skill 机制：description 是检索命脉）
                    doc = WikiDocument(
                        id=f"obs-{uuid.uuid4().hex[:8]}",
                        agent_id=agent_id,
                        title=d.title or "观察知识",
                        description=(d.description or d.title or "")[:100],
                        content=d.content,
                        tags=[],
                        extra_meta={"source": "observation", "evidence": d.evidence or []},
                    )
                    await self._wiki.create(doc)
                    decision_target = "wiki"
                else:
                    # dedupe check
                    tk = self._title_key(d.title)
                    # 去重比对必须看见 pending/flagged 记忆——这不是召回，
                    # 施加审核过滤会让同名 pending 记忆重复入库（N6 修复的边界）
                    existing = await self._store.search(
                        tk, agent_id, top_k=3, apply_review_filter=False
                    )
                    for mem in existing:
                        if self._title_key(mem.title) == tk:
                            d.action = DecisionAction.MERGE
                            d.merge_with_id = mem.id
                            break

            if d.action in (DecisionAction.STORE, DecisionAction.MERGE) and d.target != "wiki":
                if d.merge_with_id:
                    existing = await self._store.get(d.merge_with_id)
                    if existing:
                        existing.content = existing.content + "\n---\n" + d.content
                        await self._store.update(existing)
                        memory_id = existing.id
                else:
                    mem = LongTermMemory(
                        agent_id=agent_id,
                        type=d.memory_type.value,
                        title=d.title,
                        content=d.content,
                        weight=1.0,
                        extra_meta=d.metadata or {},
                    )
                    await self._store.create(mem)
                    memory_id = mem.id
        except Exception as exc:
            d.action = DecisionAction.FAILED
            error_msg = str(exc)
            logger.exception("Commit failed for event %s", d.event_id)

        # always log
        log = LearningLog(
            agent_id=agent_id,
            session_id=session_id,
            source=source,
            event_type=event_type,
            event_context=d.content[:500],
            decision_action=d.action.value,
            decision_target=decision_target,
            memory_id=memory_id,
            llm_raw_response=llm_raw_response or None,
            analyzer_mode=learning_mode,
            error_message=error_msg,
        )
        self._s.add(log)
