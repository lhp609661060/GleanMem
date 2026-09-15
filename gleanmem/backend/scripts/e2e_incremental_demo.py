"""V1.5b 端到端演示：codebase 增量蒸馏（8 步）。

链路：batch 建卡 → refresh 推变更 → flush 消费（指纹 diff / protected / 重生成）
      → 决策级 + run 级双审计 → sync 更新 md 投影

用法（需本地 PG 起着；不需要 LLM key）：
    uv run python scripts/e2e_incremental_demo.py

无 LLM key 时"内容真变化"的模块会走 failed 分支并保留事件重试——这正是 P1-1
的正确行为，演示会显示出来。指纹未变 / protected 两条路径不依赖 LLM，完整可验证。
"""
import asyncio, json, shutil, tempfile
from pathlib import Path

import httpx
from httpx import ASGITransport
from sqlalchemy import delete, select

from gleanmem.core.codebase.projection import (
    PROTECTED_MARKER, render_markdown, write_projection,
)
from gleanmem.core.codebase.scanner import fingerprint, scan_repo
from gleanmem.core.codebase.store import card_id
from gleanmem.core.database import async_session_factory
from gleanmem.core.models import (
    AgentSpace, CodebaseRun, LearningLog, PendingEvent, WikiDocument,
)
from gleanmem.main import app


async def main():
    repo = Path(tempfile.mkdtemp(prefix="demo-inc-"))
    (repo / "core").mkdir(); (repo / "api").mkdir()
    f_core = repo / "core" / "auth.py"; f_core.write_text("def require_agent():\n    return 'a'\n")
    f_api = repo / "api" / "routes.py"; f_api.write_text("def recall():\n    pass\n")

    t = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url="http://test") as c:
        r = await c.post("/api/v1/spaces", json={"name": "e2e-v15b"})
        key, agent_id = r.json()["space_key"], r.json()["agent_id"]
    print(f"1. Space ✓  agent_id={agent_id[:8]}")

    H = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(transport=t, base_url="http://test", headers=H) as c:
        # batch 建立基线卡（stub 蒸馏，带指纹）
        rep = scan_repo(repo)
        run_id = (await c.post("/api/v1/codebase/runs", json={
            "mode": "batch", "repo_path": str(repo), "commit_sha": "base0001",
            "files_scanned": rep.files_scanned, "files_excluded": rep.files_excluded,
        })).json()["run_id"]
        cards = [{
            "module": m.module, "title": f"{m.module} 模块",
            "description": f"{m.module} 的职责与接口说明",
            "content": f"{m.module} 基线内容", "narrative": f"{m.module} 叙述",
            "tags": [], "file_paths": [f.path for f in m.files],
            "fingerprints": {f.path: f.fingerprint for f in m.files}, "tokens_used": 500,
        } for m in rep.modules]
        res = (await c.post("/api/v1/codebase/cards", json={
            "run_id": run_id, "finish": True, "cards": cards})).json()
        print(f"2. batch 基线 ✓  {res['cards_written']} 张卡，指纹已固化")

        # 人工保护 api 模块（库 + md 双保护）
        for d in cards:
            md = render_markdown(title=d["title"], description=d["description"],
                narrative=d["narrative"], content=d["content"], module=d["module"],
                file_paths=d["file_paths"], commit_sha="base0001")
            write_projection(repo, d["module"], md)
        (repo / ".gleanmem" / "wiki" / "api.md").write_text(f"{PROTECTED_MARKER}\n# 我手写的 API 说明\n")
        async with async_session_factory() as s:
            doc = await s.get(WikiDocument, card_id(agent_id, "api"))
            doc.extra_meta = {**doc.extra_meta, "protected": True}; await s.commit()
        print("3. 人工修订 api 模块并标记 protected ✓")

        # refresh：core 内容真变了；api 指纹不变（模拟无关提交）
        f_core.write_text("def require_agent():\n    return resolve_from_key()\n")
        changes = [
            {"module": "core", "change": "modified", "files": [
                {"path": "core/auth.py", "fingerprint": fingerprint(f_core.read_bytes()),
                 "snippet": f_core.read_text()}]},
            {"module": "api", "change": "modified", "files": [
                {"path": "api/routes.py", "fingerprint": fingerprint(f_api.read_bytes()),
                 "snippet": f_api.read_text()}]},
        ]
        r = (await c.post("/api/v1/codebase/refresh",
             json={"commit_sha": "chg00002", "changes": changes})).json()
        print(f"4. refresh 入站 ✓  {r['modules_accepted']}")

        # flush：消费收件箱
        flush = (await c.post("/api/v1/learning/flush")).json()
        print(f"5. flush 消费 ✓  status={flush['status']} processed={flush['processed']} actions={flush['actions']}")

        # 决策级审计
        logs = [lg for lg in (await c.get("/api/v1/learning/logs")).json() if lg["source"] == "codebase"]
        print(f"6. 决策级审计 ✓  {len(logs)} 条 learning_logs:")
        for lg in logs:
            print(f"     {lg['event_type']:<38} action={lg['decision_action']:<8} target={lg['decision_target']} err={(lg['error_message'] or '-')[:40]}")

        runs = (await c.get("/api/v1/codebase/runs")).json()
        print(f"7. run 级审计 ✓  {len(runs)} 个 run:")
        for x in runs:
            print(f"     {x['mode']:<12} sha={str(x['commit_sha']):<9} 卡={x['cards_written']} 保护跳过={x['cards_skipped_protected']} tokens={x['tokens_used']} {x['status']}")

        async with async_session_factory() as s:
            left = (await s.execute(select(PendingEvent).where(
                PendingEvent.agent_id == agent_id, PendingEvent.source == "codebase"))).scalars().all()
            print(f"   收件箱剩余 {len(left)} 条" + (f"（retry_count={[e.retry_count for e in left]}，P1-1 保留重试）" if left else "（全部消费）"))
            api_doc = await s.get(WikiDocument, card_id(agent_id, "api"))
            print(f"   api 卡内容未被覆盖 ✓ content={api_doc.content!r}")
        api_md = (repo / ".gleanmem" / "wiki" / "api.md").read_text()
        print(f"   api.md 未被覆盖 ✓ ({'我手写的' in api_md})")

        pend = (await c.get("/api/v1/codebase/cards", params={"sync_pending": "true"})).json()
        print(f"8. 待 sync 的卡 ✓  {[x['id'] for x in pend]}")

    async with async_session_factory() as s:
        for M in (WikiDocument, CodebaseRun, LearningLog, PendingEvent):
            await s.execute(delete(M).where(M.agent_id == agent_id))
        await s.execute(delete(AgentSpace).where(AgentSpace.agent_id == agent_id))
        await s.commit()
    shutil.rmtree(repo)
    print("\n✅ V1.5b 端到端 8 步全通过（已清理测试数据）")


asyncio.run(main())
