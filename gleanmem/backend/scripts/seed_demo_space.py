"""前端演示数据：建一个 Space 并灌入代表性数据，输出 space_key。

用法（需本地 PG + 后端已起）：
    uv run python scripts/seed_demo_space.py

产出覆盖管理端 5 个页面的全部形态：
- 记忆与审核：chat/observation 学来的记忆（pending 但可召回）+ 一条 pattern（pending 不可召回）
- 学习日志：flush 产生的决策记录
- 代码库知识卡：一张普通卡（待 sync）+ 一张 protected 卡（不被覆盖）
- 蒸馏审计：一条 batch run（含真实实测数字：44 有效文件 / 排除 7458 / 40482 token）
- 检索预览：上述记忆可被 recall 命中；审核 pattern 后它才会出现

打印的 space_key 粘进前端登录页即可。清理见脚本末尾提示。
"""
from __future__ import annotations

import asyncio
import sys

import httpx

from gleanmem.core.codebase.store import card_id
from gleanmem.core.database import async_session_factory
from gleanmem.core.models import CodebaseRun, LongTermMemory, WikiDocument

BASE = "http://localhost:8000"


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        try:
            r = await c.post("/api/v1/spaces", json={"name": "前端演示 Space"})
        except httpx.ConnectError:
            print(f"✗ 连不上后端 {BASE}，先运行 `uv run gleanmem`", file=sys.stderr)
            return 1
        key, aid = r.json()["space_key"], r.json()["agent_id"]
        H = {"Authorization": f"Bearer {key}"}

        for ctx in [
            "发货单确认后不可修改，需走撤销流程",
            "客户偏好顺丰，加急件优先",
        ]:
            await c.post(
                "/api/v1/learning/events",
                json={"type": "user_feedback", "context": ctx},
                headers=H,
            )
        await c.post(
            "/api/v1/observations",
            json={
                "event_id": "demo-ship-1",
                "entity": "shipment",
                "event": "entity_changed",
                "change": {"from": "待确认", "to": "已发货"},
            },
            headers=H,
        )
        await c.post("/api/v1/learning/flush", headers=H)

    async with async_session_factory() as s:
        # pattern：归纳类，pending → 演示 N6「未过审不召回」
        s.add(
            LongTermMemory(
                agent_id=aid,
                type="pattern",
                title="归纳规则：发货单状态机不可逆",
                content="从已发货不能回退到待确认，需先撤销。依据 12 条观察事件。",
                review_status="pending",
                weight=1.0,
                extra_meta={"evidence_ids": ["demo-ship-1"], "induced_from": "observation"},
            )
        )
        run = CodebaseRun(
            agent_id=aid, mode="batch", repo_path="/demo/repo", commit_sha="demo0001",
            files_scanned=44, files_excluded=7458, cards_written=2,
            cards_skipped_protected=1, tokens_used=40482, model="deepseek-chat",
            status="succeeded",
        )
        s.add(run)
        await s.flush()

        s.add(
            WikiDocument(
                id=card_id(aid, "core/auth"), agent_id=aid, title="core/auth —— 认证",
                description="负责 API Key 解析与 Space 身份校验的认证模块",
                content="- 对外接口：require_agent\n- 约定：身份只在接入层解析",
                tags=["认证", "API Key"],
                extra_meta={
                    "source": "codebase", "run_id": run.id, "module": "core/auth",
                    "commit_sha": "demo0001", "file_paths": ["core/auth/deps.py"],
                    "narrative": "认证入口，读代码从 deps.py 进入。",
                    "wiki_sync_pending": True,
                },
            )
        )
        s.add(
            WikiDocument(
                id=card_id(aid, "core/wiki"), agent_id=aid, title="core/wiki —— 文档存储",
                description="WikiStore 提供 wiki 文档的增删改查与 tsvector 全文检索",
                content="人工修订过的内容（protected，自动蒸馏不会覆盖）",
                tags=["wiki", "检索"],
                extra_meta={
                    "source": "codebase", "run_id": run.id, "module": "core/wiki",
                    "commit_sha": "demo0001", "file_paths": ["core/wiki/db_store.py"],
                    "narrative": "检索层。", "protected": True, "wiki_sync_pending": False,
                },
            )
        )
        await s.commit()

    print("演示数据就绪。粘贴以下 key 到前端登录页（http://localhost:5173）：\n")
    print(f"  {key}\n")
    print("建议演示动线：")
    print("  1. 记忆与审核 → 看 pattern 那条 recallable=否")
    print("  2. 检索预览 → 搜「发货单状态怎么流转」，结果里没有该 pattern")
    print("  3. 回记忆页点「通过」→ 再检索，pattern 出现在结果里（N6 生效）")
    print("  4. 代码库知识卡 → protected 卡与待 sync 卡")
    print("  5. 蒸馏审计 → run 级审计（排除 7458 文件即体积治理）")
    print(f"\n清理：uv run python scripts/seed_demo_space.py --purge {aid}")
    return 0


async def purge(agent_id: str) -> int:
    from sqlalchemy import delete

    from gleanmem.core.models import (
        AgentSpace, LearningLog, PendingEvent,
    )

    async with async_session_factory() as s:
        for M in (WikiDocument, CodebaseRun, LearningLog, LongTermMemory, PendingEvent):
            await s.execute(delete(M).where(M.agent_id == agent_id))
        await s.execute(delete(AgentSpace).where(AgentSpace.agent_id == agent_id))
        await s.commit()
    print(f"已清理 Space {agent_id}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--purge":
        sys.exit(asyncio.run(purge(sys.argv[2])))
    sys.exit(asyncio.run(main()))
