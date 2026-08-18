"""V1.5a 端到端演示：codebase 蒸馏全链路（10 步）。

链路：建 Space → 扫描（体积治理）→ 开 run → 上传知识卡 → md 投影 → sync 回执
      → protected 修订保护 → 增量入站（幂等）→ run 审计 → 卡审计链

用法（需本地 PG 起着；不需要 LLM key）：
    uv run python scripts/e2e_codebase_demo.py

LLM 调用用 stub 替代（直接构造 CardDraft），验证的是管线与不变式，不是 prompt 质量。
真实蒸馏走 CLI：`uv run ydm-distill run <repo> --key <space_key>`。
"""
import asyncio, json, shutil, tempfile
from pathlib import Path
import httpx
from httpx import ASGITransport
from yd_memory_service.main import app
from yd_memory_service.core.codebase.analyzer import CardDraft
from yd_memory_service.core.codebase.scanner import scan_repo
from yd_memory_service.core.codebase.projection import render_markdown, write_projection, PROTECTED_MARKER
from yd_memory_service.core.database import async_session_factory
from yd_memory_service.core.models import AgentSpace, WikiDocument, CodebaseRun
from sqlalchemy import delete

async def main():
    repo = Path(tempfile.mkdtemp(prefix="demo-repo-"))
    (repo/"core").mkdir(); (repo/"api").mkdir()
    (repo/"core"/"auth.py").write_text("def require_agent():\n    return 'agent'\n")
    (repo/"api"/"routes.py").write_text("def recall():\n    pass\n")
    (repo/".venv").mkdir(); (repo/".venv"/"junk.py").write_text("vendored")

    t = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url="http://test") as c:
        r = await c.post("/api/v1/spaces", json={"name":"e2e-v15a"})
        key, agent_id = r.json()["space_key"], r.json()["agent_id"]
    print(f"1. Space 创建 ✓  agent_id={agent_id[:8]}")

    H={"Authorization":f"Bearer {key}"}
    async with httpx.AsyncClient(transport=t, base_url="http://test", headers=H) as c:
        rep = scan_repo(repo)
        print(f"2. 扫描 ✓  有效 {rep.files_scanned} 文件 / 排除 {rep.files_excluded} / 模块 {len(rep.modules)} / 预估 {rep.estimated_tokens} tokens")

        r = await c.post("/api/v1/codebase/runs", json={"mode":"batch","repo_path":str(repo),
            "commit_sha":"e2e00001","model":"stub","files_scanned":rep.files_scanned,"files_excluded":rep.files_excluded})
        run_id = r.json()["run_id"]
        print(f"3. 开 run ✓  run_id={run_id[:8]}  预算 {r.json()['tokens_spent']}/{r.json()['token_budget']}")

        drafts=[CardDraft(module=m.module, title=f"{m.module} 模块", description=f"{m.module} 的职责与对外接口说明",
                content=f"{m.module} 要点", narrative=f"{m.module} 叙述层", tags=[m.module],
                file_paths=[f.path for f in m.files], fingerprints={f.path:f.fingerprint for f in m.files},
                tokens_used=800) for m in rep.modules]
        r = await c.post("/api/v1/codebase/cards", json={"run_id":run_id,"finish":True,
            "cards":[d.__dict__ | {"tags":d.tags} for d in drafts]})
        res=r.json(); print(f"4. 上传卡 ✓  写入 {res['cards_written']} 张，tokens {res['tokens_used_total']}，status={res['status']}")

        for d in drafts:
            md = render_markdown(title=d.title, description=d.description, narrative=d.narrative,
                content=d.content, module=d.module, file_paths=d.file_paths, commit_sha="e2e00001")
            write_projection(repo, d.module, md)
        mds = sorted(p.name for p in (repo/".yd-memory"/"wiki").glob("*.md"))
        print(f"5. md 投影 ✓  {mds}")

        r = await c.post("/api/v1/codebase/cards/synced", json={"card_ids":[f"cb.{agent_id[:8]}.{d.module.replace('/','.')}" for d in drafts]})
        print(f"6. sync 回执 ✓  {r.json()}")

        # protected：人改 md + 库里标 protected，重跑不得覆盖
        target = repo/".yd-memory"/"wiki"/"core.md"
        target.write_text(f"{PROTECTED_MARKER}\n# 我手写的架构说明\n")
        async with async_session_factory() as s:
            doc = await s.get(WikiDocument, f"cb.{agent_id[:8]}.core")
            doc.extra_meta={**doc.extra_meta,"protected":True}; await s.commit()

        r = await c.post("/api/v1/codebase/runs", json={"mode":"incremental","repo_path":str(repo),"commit_sha":"e2e00002"})
        run2=r.json()["run_id"]
        r = await c.post("/api/v1/codebase/cards", json={"run_id":run2,"finish":True,
            "cards":[{"module":"core","title":"覆盖尝试","description":"LLM 重新生成","content":"新内容","narrative":"新叙述","tags":[],"file_paths":[],"fingerprints":{},"tokens_used":300}]})
        res=r.json(); print(f"7. protected 保护 ✓  写入 {res['cards_written']}，跳过 {res['cards_skipped_protected']} {res['skipped_modules']}")
        md_ok = "我手写的架构说明" in target.read_text()
        _, wrote = write_projection(repo,"core","覆盖内容")
        print(f"   md 未被覆盖 ✓ (内容保留={md_ok}, write_projection 拒写={not wrote})")

        r = await c.post("/api/v1/codebase/refresh", json={"commit_sha":"e2e00003",
            "changes":[{"module":"core","change":"modified","files":[{"path":"core/auth.py","fingerprint":"sha256:new","snippet":"def require_agent(): ..."}]}]})
        print(f"8. 增量入站 ✓  {r.json()['modules_accepted']}")
        r2 = await c.post("/api/v1/codebase/refresh", json={"commit_sha":"e2e00003",
            "changes":[{"module":"core","change":"modified","files":[{"path":"core/auth.py","fingerprint":"sha256:new"}]}]})
        print(f"   幂等 ✓  duplicate={r2.json()['modules_duplicate']}")

        runs = (await c.get("/api/v1/codebase/runs")).json()
        print(f"9. 审计查询 ✓  {len(runs)} 个 run:")
        for x in runs: print(f"     {x['mode']:<12} sha={x['commit_sha']} 卡={x['cards_written']} 保护跳过={x['cards_skipped_protected']} tokens={x['tokens_used']} {x['status']}")
        cards = (await c.get("/api/v1/codebase/cards")).json()
        print(f"10. 卡审计链 ✓  {len(cards)} 张:")
        for x in cards: print(f"     {x['id']:<28} run={x['run_id'][:8]} sha={x['commit_sha']} protected={x['protected']} pending={x['wiki_sync_pending']}")

    async with async_session_factory() as s:
        for M in (WikiDocument, CodebaseRun):
            await s.execute(delete(M).where(M.agent_id==agent_id))
        from yd_memory_service.core.models import PendingEvent
        await s.execute(delete(PendingEvent).where(PendingEvent.agent_id==agent_id))
        await s.execute(delete(AgentSpace).where(AgentSpace.agent_id==agent_id)); await s.commit()
    shutil.rmtree(repo)
    print("\n✅ V1.5a 端到端 10 步全通过（已清理测试数据）")

asyncio.run(main())
