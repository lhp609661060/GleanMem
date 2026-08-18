"""真实 LLM 蒸馏验证：跑一次全量，评估产出质量 + 中文召回率。

这是 V1.5 唯一需要真实 LLM key 的验证环节（管线逻辑已由 45 个测试覆盖）。
目的不是测管线，是回答两个问题：
  1. LLM 产出的 description 够不够检索？（Skill 机制的命脉）
  2. 蒸馏出的知识卡能否被自然语言查询命中？（对齐 P0-3 的评估方法）

前置：
  - backend/.env 里配好 YDM_LLM_API_KEY / API_BASE / MODEL（见 .env.example）
  - 本地 PG 起着

用法：
    uv run python scripts/verify_real_distill.py [目标仓库路径]

默认蒸馏 backend/src/yd_memory_service（本项目自身的源码，约 20 个模块，
规模适合演示且内容我们熟悉，便于人工判断产出质量）。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx
from httpx import ASGITransport
from sqlalchemy import delete

from yd_memory_service.config import settings
from yd_memory_service.core.codebase.analyzer import CodebaseAnalyzer
from yd_memory_service.core.codebase.scanner import scan_repo
from yd_memory_service.core.database import async_session_factory
from yd_memory_service.core.models import AgentSpace, CodebaseRun, WikiDocument
from yd_memory_service.core.wiki.db_store import WikiStore
from yd_memory_service.main import app

# 召回评估：自然语言查询 → 可接受命中的模块（对齐 P0-3 的 20 query 方法）
# 期望值是**集合**：同一能力在多个模块都有实现时，命中任一即算对
# （首轮实测教训：把"中文全文检索"写死成 core/long_term 会误判 core/wiki 为 miss，
#  而后者的 WikiStore 同样实现了 tsvector 检索——评估脚本的锅，不是产出的锅）
RECALL_QUERIES = [
    ("怎么做身份认证和 API Key 校验", {"api"}),
    ("学习管线怎么处理待办事件", {"core"}),
    ("代码库蒸馏是怎么实现的", {"core/codebase"}),
    ("中文全文检索怎么做的", {"core/long_term", "core/wiki"}),
    ("MCP 工具有哪些", {"mcp"}),
    ("记忆怎么排序和重排", {"orchestrator"}),
    ("数据库表结构定义在哪", {"core/models"}),
    ("命令行工具怎么用", {"cli"}),
    ("wiki 文档怎么存储和检索", {"core/wiki"}),
    ("观察事件的入站接口", {"api"}),
]


async def main() -> int:
    if not settings.llm_api_key:
        print("✗ 未配置 YDM_LLM_API_KEY。", file=sys.stderr)
        print("  在 backend/.env 中填入（参考 .env.example）后重跑。", file=sys.stderr)
        return 1

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "src/yd_memory_service").resolve()
    print(f"目标仓库：{target}")
    print(f"模型：{settings.llm_model}  base={settings.llm_api_base}\n")

    report = scan_repo(target)
    print(report.summary())
    print(f"\n即将真实调用 LLM {len(report.modules)} 次。")

    t = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url="http://test") as c:
        r = await c.post("/api/v1/spaces", json={"name": "verify-real-distill"})
        key, agent_id = r.json()["space_key"], r.json()["agent_id"]

    H = {"Authorization": f"Bearer {key}"}
    analyzer = CodebaseAnalyzer()
    started = time.time()

    async with httpx.AsyncClient(transport=t, base_url="http://test", headers=H, timeout=300) as c:
        run_id = (await c.post("/api/v1/codebase/runs", json={
            "mode": "batch", "repo_path": str(target), "commit_sha": "verify01",
            "model": analyzer.model, "files_scanned": report.files_scanned,
            "files_excluded": report.files_excluded,
        })).json()["run_id"]

        sem = asyncio.Semaphore(4)

        async def one(m):
            async with sem:
                d = await analyzer.distill(m, str(target))
                mark = "✓" if d else "✗"
                extra = f"{d.tokens_used:>6} tok  desc={len(d.description)}字" if d else "失败"
                print(f"  {mark} {m.module:<45} {extra}")
                return d

        print(f"\n蒸馏中（并发 4）：")
        drafts = [d for d in await asyncio.gather(*(one(m) for m in report.modules)) if d]
        elapsed = time.time() - started

        if not drafts:
            print("\n✗ 全部失败，检查 key / base / 模型名。", file=sys.stderr)
            return 1

        res = (await c.post("/api/v1/codebase/cards", json={
            "run_id": run_id, "finish": True,
            "cards": [{
                "module": d.module, "title": d.title, "description": d.description,
                "content": d.content, "narrative": d.narrative, "tags": d.tags,
                "file_paths": d.file_paths, "fingerprints": d.fingerprints,
                "tokens_used": d.tokens_used,
            } for d in drafts],
        })).json()

        total_tokens = sum(d.tokens_used for d in drafts)
        print(f"\n{'='*70}")
        print(f"蒸馏完成：{len(drafts)}/{len(report.modules)} 模块成功")
        print(f"  耗时 {elapsed:.1f}s   token {total_tokens:,}（预估 {report.estimated_tokens:,}）")
        print(f"  知识卡写入 {res['cards_written']} 张")
        print(f"  description 长度：min={min(len(d.description) for d in drafts)} "
              f"max={max(len(d.description) for d in drafts)} "
              f"avg={sum(len(d.description) for d in drafts)/len(drafts):.0f}")

        print(f"\n{'='*70}\n产出样本（前 3 张卡，人工判断质量）：")
        for d in drafts[:3]:
            print(f"\n── {d.module} ──")
            print(f"  title: {d.title}")
            print(f"  desc : {d.description}")
            print(f"  tags : {d.tags}")
            print(f"  content: {d.content[:200]}{'…' if len(d.content) > 200 else ''}")

        # 召回评估：description 是否真的可检索
        print(f"\n{'='*70}\n中文召回评估（{len(RECALL_QUERIES)} query，Top3 命中期望模块）：")
        hits = 0
        async with async_session_factory() as s:
            wiki = WikiStore(s)
            for query, expect in RECALL_QUERIES:
                docs = await wiki.search(query, agent_id, top_k=3, source="codebase")
                mods = [(d.extra_meta or {}).get("module", "?") for d in docs]
                hit = any(
                    m in expect or any(str(m).startswith(e) for e in expect) for m in mods
                )
                hits += hit
                print(f"  {'✓' if hit else '✗'} {query:<28} → {mods}")
        rate = hits / len(RECALL_QUERIES) * 100
        print(f"\n召回率：{hits}/{len(RECALL_QUERIES)} = {rate:.0f}%（P0-3 阈值 ≥40%）")

        print(f"\n{'='*70}")
        print("结论：" + (
            f"✅ 产出可用，召回 {rate:.0f}%" if rate >= 40
            else f"⚠️ 召回仅 {rate:.0f}%，description 生成策略需调整（见 01-design 核心决策 #8）"
        ))
        print(f"\n数据保留在 Space {agent_id}（key 前缀 {key[:12]}…）以便人工查看。")
        print(f"清理：uv run python -c \"import asyncio;from scripts.verify_real_distill import cleanup;asyncio.run(cleanup('{agent_id}'))\"")
    return 0


async def cleanup(agent_id: str) -> None:
    async with async_session_factory() as s:
        for M in (WikiDocument, CodebaseRun):
            await s.execute(delete(M).where(M.agent_id == agent_id))
        await s.execute(delete(AgentSpace).where(AgentSpace.agent_id == agent_id))
        await s.commit()
    print(f"已清理 Space {agent_id}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
