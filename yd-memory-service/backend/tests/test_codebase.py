"""V1.5a：codebase 蒸馏——扫描/体积治理、run 审计、protected 保护、md 投影、增量入站。

覆盖的不变式：
- 体积治理：vendored 依赖必须被排除（否则一次全量烧光预算）
- protected 跳过：自动更新绝不覆盖人的修订（与增量捆绑发布，Qoder 教训）
- 审计链：卡 metadata.run_id → codebase_runs → commit SHA → 文件路径
- md 是单向投影：本地 protected 标记的文件不被覆盖
- refresh 幂等：同 commit + 同模块重复推送只进一条收件箱事件
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from yd_memory_service.core.codebase.analyzer import CardDraft, _parse_card_json
from yd_memory_service.core.codebase.projection import (
    PROTECTED_MARKER,
    module_to_filename,
    render_markdown,
    write_projection,
)
from yd_memory_service.core.codebase.scanner import scan_repo
from yd_memory_service.core.codebase.store import CodebaseStore, card_id
from yd_memory_service.core.database import async_session_factory
from yd_memory_service.core.models import CodebaseRun, WikiDocument
from yd_memory_service.core.wiki.db_store import WikiStore
from yd_memory_service.main import app


def _client(key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    )


def _draft(module: str = "core/auth", **kw) -> CardDraft:
    defaults = dict(
        module=module,
        title=f"{module} —— 身份认证模块",
        description="负责 API Key 解析与 Space 身份校验的认证模块",
        content="- 对外接口：require_agent\n- 约定：身份只在接入层解析",
        narrative="这个模块是认证入口，读代码从 deps.py 进入。",
        tags=["认证", "API Key"],
        file_paths=[f"{module}/deps.py"],
        fingerprints={f"{module}/deps.py": "sha256:abc"},
        tokens_used=1200,
    )
    defaults.update(kw)
    return CardDraft(**defaults)


# -- 扫描器 / 体积治理 -------------------------------------------------------

def test_scanner_excludes_vendored_and_aggregates_modules(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "src" / "util.py").write_text("x = 1", encoding="utf-8")
    # 必须被排除的：vendored 目录、锁文件、非源码后缀
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "dep.py").write_text("vendored", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("vendored", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("locked", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")

    report = scan_repo(tmp_path)

    assert report.files_scanned == 2
    assert report.files_excluded == 4
    assert [m.module for m in report.modules] == ["src"]
    assert report.estimated_tokens > 0
    # 指纹是增量 diff 的比较基准
    assert all(f.fingerprint.startswith("sha256:") for f in report.modules[0].files)


def test_scanner_dry_run_summary_reports_budget_inputs(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n" * 100, encoding="utf-8")
    report = scan_repo(tmp_path)
    summary = report.summary()
    assert "有效文件 1 个" in summary
    assert "预估 token" in summary


# -- run 审计 + protected 跳过 ----------------------------------------------

async def test_batch_run_writes_audit_chain(space_client):
    key, agent_id = await space_client()
    async with _client(key) as client:
        r = await client.post(
            "/api/v1/codebase/runs",
            json={
                "mode": "batch",
                "repo_path": "/tmp/demo-repo",
                "commit_sha": "abc1234",
                "model": "gpt-4o-mini",
                "files_scanned": 68,
                "files_excluded": 7458,
            },
        )
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]

        up = await client.post(
            "/api/v1/codebase/cards",
            json={
                "run_id": run_id,
                "finish": True,
                "cards": [
                    {
                        "module": "core/auth",
                        "title": "core/auth —— 认证",
                        "description": "API Key 解析与身份校验",
                        "content": "要点",
                        "narrative": "叙述",
                        "tags": ["认证"],
                        "file_paths": ["core/auth/deps.py"],
                        "fingerprints": {"core/auth/deps.py": "sha256:abc"},
                        "tokens_used": 1500,
                    }
                ],
            },
        )
        assert up.status_code == 200, up.text
        assert up.json()["cards_written"] == 1
        assert up.json()["status"] == "succeeded"

        runs = (await client.get("/api/v1/codebase/runs")).json()
        assert len(runs) == 1
        assert runs[0]["files_excluded"] == 7458  # 体积治理可见性
        assert runs[0]["tokens_used"] == 1500
        assert runs[0]["finished_at"] is not None

    # 审计链：卡 → run → commit SHA → 文件
    async with async_session_factory() as s:
        doc = await s.get(WikiDocument, card_id(agent_id, "core/auth"))
        assert doc is not None
        assert doc.extra_meta["run_id"] == run_id
        assert doc.extra_meta["commit_sha"] == "abc1234"
        assert doc.extra_meta["file_paths"] == ["core/auth/deps.py"]
        assert doc.extra_meta["source"] == "codebase"
        assert doc.extra_meta["wiki_sync_pending"] is True


async def test_protected_card_is_never_overwritten(make_space):
    """自动更新必须跳过 protected——V1.5 的核心承诺。"""
    agent_id = await make_space()
    async with async_session_factory() as s:
        store = CodebaseStore(s)
        run = await store.start_run(agent_id=agent_id, mode="batch", repo_path="/r")
        cid = await store.upsert_card(agent_id=agent_id, draft=_draft(), run_id=run.id)
        assert cid is not None
        await s.commit()

    # 人工修订 + 标记保护
    async with async_session_factory() as s:
        doc = await s.get(WikiDocument, cid)
        doc.content = "人工修订过的内容"
        doc.extra_meta = {**doc.extra_meta, "protected": True}
        await s.commit()

    # 再次自动蒸馏同一模块 → 必须跳过
    async with async_session_factory() as s:
        store = CodebaseStore(s)
        run2 = await store.start_run(agent_id=agent_id, mode="incremental", repo_path="/r")
        skipped = await store.upsert_card(
            agent_id=agent_id,
            draft=_draft(content="LLM 重新生成的内容"),
            run_id=run2.id,
        )
        await s.commit()
        assert skipped is None

    async with async_session_factory() as s:
        doc = await s.get(WikiDocument, cid)
        assert doc.content == "人工修订过的内容"
        assert doc.extra_meta["protected"] is True


async def test_run_scoped_by_agent(space_client):
    """run 归属校验：拿别的 Space 的 run_id 上传卡必须失败。"""
    key_a, _ = await space_client()
    key_b, _ = await space_client()
    async with _client(key_a) as ca:
        run_id = (
            await ca.post(
                "/api/v1/codebase/runs", json={"mode": "batch", "repo_path": "/a"}
            )
        ).json()["run_id"]
    async with _client(key_b) as cb:
        r = await cb.post(
            "/api/v1/codebase/cards", json={"run_id": run_id, "cards": []}
        )
        assert r.status_code == 404


async def test_token_budget_blocks_new_run(space_client):
    """token 硬预算用尽 → 拒绝开新 run（D14）。"""
    key, agent_id = await space_client({"codebase_token_budget": 1000})
    async with async_session_factory() as s:
        s.add(
            CodebaseRun(
                agent_id=agent_id, mode="batch", repo_path="/r",
                tokens_used=1200, status="succeeded",
            )
        )
        await s.commit()
    async with _client(key) as client:
        r = await client.post(
            "/api/v1/codebase/runs", json={"mode": "batch", "repo_path": "/r"}
        )
        assert r.status_code == 429
        assert "预算" in r.json()["detail"]


async def test_codebase_endpoints_require_auth():
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/api/v1/codebase/runs", "/api/v1/codebase/cards"):
            assert (await client.get(path)).status_code == 401


# -- md 投影（D11：单向、可重建、本地 protected 优先）-----------------------

def test_projection_writes_and_respects_local_protected(tmp_path):
    md = render_markdown(
        title="core/auth —— 认证", description="身份校验", narrative="叙述层",
        content="知识卡正文", module="core/auth",
        file_paths=["core/auth/deps.py"], commit_sha="abc1234",
    )
    path, ok = write_projection(tmp_path, "core/auth", md)
    assert ok and path.exists()
    assert path.name == module_to_filename("core/auth")
    assert "core/auth —— 认证" in path.read_text(encoding="utf-8")

    # 人手改了 md 并加保护标记 → sync 不得覆盖
    path.write_text(f"{PROTECTED_MARKER}\n# 我自己写的\n", encoding="utf-8")
    _, ok2 = write_projection(tmp_path, "core/auth", md)
    assert ok2 is False
    assert "我自己写的" in path.read_text(encoding="utf-8")


async def test_sync_pending_flow_marks_synced(make_space):
    agent_id = await make_space()
    async with async_session_factory() as s:
        store = CodebaseStore(s)
        run = await store.start_run(agent_id=agent_id, mode="batch", repo_path="/r")
        cid = await store.upsert_card(agent_id=agent_id, draft=_draft(), run_id=run.id)
        await s.commit()

    async with async_session_factory() as s:
        store = CodebaseStore(s)
        pending = await store.list_cards(agent_id, sync_pending=True)
        assert [d.id for d in pending] == [cid]
        assert await store.mark_synced(agent_id, [cid]) == 1
        await s.commit()

    async with async_session_factory() as s:
        store = CodebaseStore(s)
        assert await store.list_cards(agent_id, sync_pending=True) == []


# -- 增量入站（V1.5b 消费，V1.5a 先把口开出来）------------------------------

async def test_refresh_is_idempotent_per_commit_and_module(space_client):
    key, agent_id = await space_client()
    body = {
        "commit_sha": "a1b2c3d",
        "changes": [
            {
                "module": "core/learning",
                "change": "modified",
                "files": [{"path": "core/learning.py", "fingerprint": "sha256:x"}],
            }
        ],
    }
    async with _client(key) as client:
        r1 = (await client.post("/api/v1/codebase/refresh", json=body)).json()
        assert r1["modules_accepted"] == ["core/learning"]
        # CI 重试同一 commit → 幂等
        r2 = (await client.post("/api/v1/codebase/refresh", json=body)).json()
        assert r2["modules_duplicate"] == ["core/learning"]

    async with async_session_factory() as s:
        from yd_memory_service.core.models import PendingEvent

        rows = (
            await s.execute(
                select(PendingEvent).where(
                    PendingEvent.agent_id == agent_id,
                    PendingEvent.source == "codebase",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "module_changed"
        assert rows[0].dedup_key == "cb:a1b2c3d:core/learning"


async def test_refresh_rejects_oversized_snippet(space_client):
    key, _ = await space_client()
    async with _client(key) as client:
        r = await client.post(
            "/api/v1/codebase/refresh",
            json={
                "commit_sha": "deadbee",
                "changes": [
                    {
                        "module": "big",
                        "files": [
                            {"path": "big.py", "fingerprint": "sha256:y", "snippet": "x" * 5000}
                        ],
                    }
                ],
            },
        )
        assert r.status_code == 413


# -- 检索配合（07 评审 §5.3：codebase 卡不得挤占业务 wiki）------------------

async def test_wiki_search_can_filter_by_source(make_space):
    agent_id = await make_space()
    async with async_session_factory() as s:
        store = CodebaseStore(s)
        run = await store.start_run(agent_id=agent_id, mode="batch", repo_path="/r")
        await store.upsert_card(
            agent_id=agent_id,
            draft=_draft(module="core/发货", description="发货单状态流转的代码实现模块"),
            run_id=run.id,
        )
        s.add(
            WikiDocument(
                id=f"biz-{agent_id[:8]}",
                agent_id=agent_id,
                title="发货业务词典",
                description="发货单状态流转的业务口径与术语",
                content="业务口径",
                extra_meta={"source": "manual"},
            )
        )
        await s.commit()

    async with async_session_factory() as s:
        wiki = WikiStore(s)
        only_cb = await wiki.search("发货单状态流转", agent_id, source="codebase")
        assert [d.extra_meta.get("source") for d in only_cb] == ["codebase"]

        no_cb = await wiki.search("发货单状态流转", agent_id, exclude_source="codebase")
        assert no_cb and all(
            d.extra_meta.get("source") != "codebase" for d in no_cb
        )


# ---------------------------------------------------------------- E1：codebase card JSON 解析失败可观测


def test_parse_card_json_logs_warning_on_invalid_json(caplog):
    """E1：_parse_card_json 解析失败时打 warning（此前静默返回 None），仍返回 None。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="yd_memory_service.core.codebase.analyzer"):
        result = _parse_card_json("definitely not json")

    assert result is None
    assert any("解析失败" in r.message for r in caplog.records)


def test_parse_card_json_silent_on_valid(caplog):
    """E1：合法 JSON 不打 warning，正常返回 dict。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="yd_memory_service.core.codebase.analyzer"):
        result = _parse_card_json('{"module": "core/发货", "description": "x"}')

    assert result == {"module": "core/发货", "description": "x"}
    assert not caplog.records
