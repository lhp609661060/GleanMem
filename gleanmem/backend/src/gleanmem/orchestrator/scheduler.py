"""内置调度器（V2）：Space 级 cron 自动 flush。

取代 V1 的「外部 crontab 调 POST /api/v1/learning/flush」。设计要点：

- **配置在 Space 上**：`config.schedule`（5 段 cron，服务器本地时区）+
  `config.schedule_enabled`（false 暂停）。一个 Space 一个定时器，与 source 正交
  （定时归纳样例 / 定时蒸馏观察都走同一条 flush）。
- **不引入 jobstore**：APScheduler 只负责「每分钟 tick 一次」，到不到点由 croniter
  对 DB 里的 schedule 判定。Space 增删改不必同步 job 列表，因此不需要 resync 端点。
- **多副本互斥**：抢到 `last_fired_slot` 的那一份才执行（单条原子 UPDATE），其余副本
  见槽位已占即跳过；Space 内部另有 flush 的 advisory lock 兜底。
- **重启补跑**：`config.catch_up=true` 时，启动后补看最近一个应当触发的分钟点，
  且在 `settings.scheduler_catchup_window_minutes` 窗口内才补——无限回溯会让一次
  周五重启补跑几周前的批次。
- **触发即留痕**：`last_scheduled_flush` 记完成时间，供 GET /schedules 观测。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from gleanmem.core.database import async_session_factory
from gleanmem.core.manager import MemoryManager
from gleanmem.core.models.agent_space import AgentSpace

logger = logging.getLogger(__name__)

SLOT_FORMAT = "%Y-%m-%dT%H:%M"


class InvalidSchedule(ValueError):
    """cron 表达式不合法（字段数或语法）。"""


def normalize_cron(expr: str) -> str:
    """校验并归一化 cron 表达式，不合法则抛 InvalidSchedule。

    只接受 5 段（分 时 日 月 周）——6 段是 Quartz 方言，静默兼容会把用户的
    「秒」语义当成「分」，宁可拒绝。
    """
    from croniter import CroniterBadCronError, croniter

    fields = expr.split()
    if len(fields) != 5:
        raise InvalidSchedule(
            f"cron 需要 5 段（分 时 日 月 周），收到 {len(fields)} 段：{expr!r}"
        )
    try:
        croniter(expr, datetime.now())
    except (CroniterBadCronError, ValueError, KeyError) as exc:
        raise InvalidSchedule(f"cron 表达式不合法：{expr!r}（{exc}）") from exc
    return " ".join(fields)


def slot_of(moment: datetime) -> str:
    return moment.strftime(SLOT_FORMAT)


def is_due(expr: str, now: datetime, last_fired_slot: str | None) -> str | None:
    """本分钟是否应当触发；返回要抢占的槽位，否则 None。

    判定按「当前落在哪一分钟」做，故一分钟内多个 tick 都会命中同一槽位，
    由 `claim_slot` 的原子 UPDATE 保证只有一个执行方（多副本同理）。
    """
    from croniter import croniter

    moment = now.replace(second=0, microsecond=0)
    if not croniter.match(expr, moment):
        return None
    slot = slot_of(moment)
    return None if last_fired_slot == slot else slot


async def _scheduled_spaces() -> list[AgentSpace]:
    stmt = select(AgentSpace).where(
        AgentSpace.status == "active",
        func.coalesce(AgentSpace.config["schedule"].astext, "") != "",
    )
    async with async_session_factory() as s:
        return list((await s.execute(stmt)).scalars().all())


async def claim_slot(agent_id: str, slot: str) -> bool:
    """原子占槽：只有把 `last_fired_slot` 改成本槽的那一次返回 True。"""
    async with async_session_factory() as s:
        result = await s.execute(
            update(AgentSpace)
            .where(
                AgentSpace.agent_id == agent_id,
                AgentSpace.last_fired_slot.is_distinct_from(slot),
            )
            .values(last_fired_slot=slot)
        )
        await s.commit()
        return bool(result.rowcount)


async def fire_flush(agent_id: str, expr: str) -> dict:
    """对单个 Space 执行一次调度 flush，并落 `last_scheduled_flush`。"""
    async with async_session_factory() as s:
        mgr = MemoryManager(s)
        try:
            result = await mgr.flush(agent_id)
            await s.commit()
        except Exception as exc:  # noqa: BLE001 - 单 Space 失败不拖垮整个 tick
            await s.rollback()
            logger.exception("Scheduled flush for %s failed", agent_id)
            result = {"status": "error", "message": str(exc)[:300]}
    if result.get("status") == "ok":
        async with async_session_factory() as s:
            await s.execute(
                update(AgentSpace)
                .where(AgentSpace.agent_id == agent_id)
                .values(last_scheduled_flush=datetime.now().astimezone())
            )
            await s.commit()
    logger.info("Scheduled flush (%s) for %s -> %s", expr, agent_id, result)
    return {"agent_id": agent_id, "cron": expr, **result}


def _valid_or_none(expr: str) -> str | None:
    try:
        return normalize_cron(expr)
    except InvalidSchedule as exc:
        logger.warning("schedule %s，跳过", exc)
        return None


async def run_due_flushes(now: datetime | None = None) -> list[dict]:
    """扫描带 schedule 的 active Space，触发到点的那些。"""
    now = now or datetime.now()
    results: list[dict] = []
    for space in await _scheduled_spaces():
        config = space.config or {}
        if config.get("schedule_enabled", True) is False:
            continue
        expr = _valid_or_none(str(config.get("schedule", "")))
        if not expr:
            continue
        slot = is_due(expr, now, space.last_fired_slot)
        if slot is None:
            continue
        if not await claim_slot(space.agent_id, slot):
            logger.info("Space %s 槽位 %s 已被其他实例占用，跳过", space.agent_id, slot)
            continue
        try:
            results.append(await fire_flush(space.agent_id, expr))
        except Exception as exc:  # noqa: BLE001 - 一个 Space 失败不影响其余 Space
            logger.exception("Scheduled flush for %s failed", space.agent_id)
            results.append(
                {"agent_id": space.agent_id, "cron": expr, "status": "error",
                 "message": str(exc)[:300]}
            )
    return results


async def catch_up_on_startup(now: datetime | None = None) -> list[dict]:
    """为 `catch_up=true` 的 Space 补跑重启期间漏掉的最近一次触发。"""
    from croniter import croniter

    now = now or datetime.now()
    window = timedelta(minutes=_catchup_window())
    results: list[dict] = []
    for space in await _scheduled_spaces():
        config = space.config or {}
        if config.get("catch_up") is not True:
            continue
        expr = _valid_or_none(str(config.get("schedule", "")))
        if not expr:
            continue
        prev = croniter(expr, now.replace(second=0, microsecond=0)).get_prev(datetime)
        if now - prev > window:
            continue  # 太久以前的槽，补跑没有意义
        slot = slot_of(prev)
        if (space.last_fired_slot or "") >= slot:
            continue
        if not await claim_slot(space.agent_id, slot):
            continue
        logger.info("Space %s 补跑漏掉的槽位 %s", space.agent_id, slot)
        results.append(await fire_flush(space.agent_id, expr))
    return results


def _catchup_window() -> int:
    from gleanmem.config import settings

    return settings.scheduler_catchup_window_minutes


# ------------------------------------------------------------------ 生命周期

_scheduler = None


async def _tick() -> None:
    try:
        await run_due_flushes()
    except Exception:  # noqa: BLE001
        logger.exception("scheduler tick failed")


def start_scheduler():
    """按 settings 启动内置调度器；返回实例，禁用或已启动时返回当前值。"""
    global _scheduler
    from gleanmem.config import settings

    if not settings.scheduler_enabled or _scheduler is not None:
        return _scheduler
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _tick,
        "interval",
        seconds=settings.scheduler_tick_seconds,
        id="gleanmem-cron-tick",
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("内置调度器已启动，tick=%ss", settings.scheduler_tick_seconds)
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
