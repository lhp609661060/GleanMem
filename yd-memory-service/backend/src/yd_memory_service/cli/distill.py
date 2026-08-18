"""ydm-distill：codebase 蒸馏客户端 CLI（V1.5a）。

服务端零仓库访问权（D11）——扫描、读代码、蒸馏、落盘 md 全在客户端；
服务端只收知识卡与审计元数据。

用法：
    # 1. dry-run：先报后跑（D14），不调 LLM、不花钱
    uv run ydm-distill scan <repo>

    # 2. 全量蒸馏 + 上传知识卡 + 落盘 md 投影
    uv run ydm-distill run <repo> --url http://localhost:8000 --key <space_key>

    # 3. 只重建 md 投影（卡是事实源，md 可随时重建）
    uv run ydm-distill sync <repo> --url ... --key ... [--all]

    # 4. 增量：把本地相对 --since 的变更推进收件箱（CI 提交后回调用）
    uv run ydm-distill refresh <repo> --url ... --key ... --since HEAD~1
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys

import httpx

from yd_memory_service.core.codebase.analyzer import CodebaseAnalyzer
from yd_memory_service.core.codebase.projection import render_markdown, write_projection
from yd_memory_service.core.codebase.scanner import scan_repo

CONCURRENCY = 4  # 单模块调用互相独立 → 可并发（D14）


def _git_sha(repo: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return out.stdout.strip()[:40] or None
    except Exception:
        return None


def cmd_scan(args: argparse.Namespace) -> int:
    report = scan_repo(args.repo)
    print(report.summary())
    print()
    for m in report.modules:
        print(f"  {m.module:<55} {len(m.files):>3} files  {m.size / 1024:>7.1f} KB")
    print()
    print("这是 dry-run，未调用 LLM、未产生费用。确认后运行 `ydm-distill run` 执行蒸馏。")
    return 0


async def _run(args: argparse.Namespace) -> int:
    report = scan_repo(args.repo)
    print(report.summary())
    if not report.modules:
        print("没有可蒸馏的模块，退出。")
        return 1

    commit_sha = _git_sha(args.repo)
    headers = {"Authorization": f"Bearer {args.key}"}
    analyzer = CodebaseAnalyzer(model=args.model)

    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=120) as http:
        resp = await http.post(
            "/api/v1/codebase/runs",
            json={
                "mode": "batch",
                "repo_path": report.repo_path,
                "commit_sha": commit_sha,
                "model": analyzer.model,
                "files_scanned": report.files_scanned,
                "files_excluded": report.files_excluded,
            },
        )
        if resp.status_code != 200:
            print(f"开 run 失败：{resp.status_code} {resp.text}", file=sys.stderr)
            return 1
        run_info = resp.json()
        run_id = run_info["run_id"]
        print(f"\nrun_id={run_id}  预算 {run_info['tokens_spent']}/{run_info['token_budget']} tokens")

        sem = asyncio.Semaphore(CONCURRENCY)

        async def distill(module):
            async with sem:
                draft = await analyzer.distill(module, report.repo_path)
                print(
                    f"  {'✓' if draft else '✗'} {module.module}"
                    + (f"  ({draft.tokens_used} tokens)" if draft else "  蒸馏失败/跳过")
                )
                return draft

        print(f"\n蒸馏 {len(report.modules)} 个模块（并发 {CONCURRENCY}）：")
        drafts = [d for d in await asyncio.gather(*(distill(m) for m in report.modules)) if d]

        if not drafts:
            print("\n无有效产物（检查 YDM_LLM_API_KEY 是否配置）。", file=sys.stderr)
            await http.post(
                "/api/v1/codebase/cards",
                json={"run_id": run_id, "cards": [], "finish": True},
            )
            return 1

        resp = await http.post(
            "/api/v1/codebase/cards",
            json={
                "run_id": run_id,
                "finish": True,
                "cards": [
                    {
                        "module": d.module,
                        "title": d.title,
                        "description": d.description,
                        "content": d.content,
                        "narrative": d.narrative,
                        "tags": d.tags,
                        "file_paths": d.file_paths,
                        "fingerprints": d.fingerprints,
                        "tokens_used": d.tokens_used,
                    }
                    for d in drafts
                ],
            },
        )
        if resp.status_code != 200:
            print(f"上传知识卡失败：{resp.status_code} {resp.text}", file=sys.stderr)
            return 1
        result = resp.json()
        print(
            f"\n知识卡：写入 {result['cards_written']} 张"
            f"，protected 跳过 {result['cards_skipped_protected']} 张"
            f"，累计 {result['tokens_used_total']} tokens"
        )
        if result["skipped_modules"]:
            print(f"  受保护未覆盖：{', '.join(result['skipped_modules'])}")

        # md 投影（卡 → md 单向，D11）
        written, protected = 0, 0
        for d in drafts:
            if d.module in result["skipped_modules"]:
                continue
            md = render_markdown(
                title=d.title, description=d.description, narrative=d.narrative,
                content=d.content, module=d.module, file_paths=d.file_paths,
                commit_sha=commit_sha,
            )
            _, ok = write_projection(args.repo, d.module, md)
            written += ok
            protected += not ok
        print(f"md 投影：写入 {written} 个文件，本地 protected 跳过 {protected} 个 → {args.repo}/.yd-memory/wiki/")
        return 0


async def _sync(args: argparse.Namespace) -> int:
    """只重建 md 投影：卡是事实源，md 可随时从卡重建（D11）。"""
    headers = {"Authorization": f"Bearer {args.key}"}
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=60) as http:
        params = {} if args.all else {"sync_pending": "true"}
        resp = await http.get("/api/v1/codebase/cards", params=params)
        if resp.status_code != 200:
            print(f"拉取知识卡失败：{resp.status_code} {resp.text}", file=sys.stderr)
            return 1
        cards = resp.json()
        if not cards:
            print("没有待 sync 的知识卡（用 --all 强制重建全部）。")
            return 0

        synced_ids, protected = [], 0
        for c in cards:
            md = render_markdown(
                title=c["title"], description=c["description"],
                narrative=c.get("narrative", ""), content=c["content"],
                module=c.get("module") or c["id"], file_paths=c.get("file_paths", []),
                commit_sha=c.get("commit_sha"),
            )
            _, ok = write_projection(args.repo, c.get("module") or c["id"], md)
            if ok:
                synced_ids.append(c["id"])
            else:
                protected += 1

        if synced_ids:
            await http.post("/api/v1/codebase/cards/synced", json={"card_ids": synced_ids})
        print(f"md 投影：写入 {len(synced_ids)} 个，本地 protected 跳过 {protected} 个")
        return 0


async def _refresh(args: argparse.Namespace) -> int:
    """增量入站（V1.5b）：git diff 出变更文件 → 按模块聚合 → 推收件箱。

    带上变更后的文件内容片段，因为服务端不读仓库（D11）。
    """
    from pathlib import Path

    from yd_memory_service.core.codebase.scanner import (
        DEFAULT_EXCLUDE_DIRS, SOURCE_SUFFIXES, fingerprint,
    )

    commit_sha = _git_sha(args.repo)
    diff = subprocess.run(
        ["git", "-C", args.repo, "diff", "--name-status", args.since, "HEAD"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if diff.returncode != 0:
        print(f"git diff 失败：{diff.stderr.strip()}", file=sys.stderr)
        return 1

    by_module: dict[str, dict] = {}
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, rel = parts[0].strip(), parts[-1].strip()
        path = Path(rel)
        if any(p in DEFAULT_EXCLUDE_DIRS for p in path.parts[:-1]):
            continue
        if path.suffix not in SOURCE_SUFFIXES:
            continue

        module = str(path.parent) if str(path.parent) != "." else "(root)"
        entry = by_module.setdefault(
            module, {"module": module, "change": "modified", "files": []}
        )
        if status.startswith("D"):
            # 文件删了：不带 snippet，指纹置空标记删除
            entry["files"].append({"path": rel, "fingerprint": "deleted"})
            continue
        abs_path = Path(args.repo) / rel
        try:
            data = abs_path.read_bytes()
        except OSError:
            continue
        snippet = data.decode("utf-8", errors="replace")
        if len(snippet.encode("utf-8")) > 4000:  # 服务端 4KB 护栏
            snippet = snippet[:3500] + "\n…（截断）"
        entry["files"].append(
            {"path": rel, "fingerprint": fingerprint(data), "snippet": snippet}
        )

    changes = [c for c in by_module.values() if c["files"]]
    if not changes:
        print(f"{args.since}..HEAD 无源码变更，无需 refresh。")
        return 0

    print(f"变更模块 {len(changes)} 个（commit {commit_sha}）：")
    for c in changes:
        print(f"  {c['module']:<50} {len(c['files'])} files")

    headers = {"Authorization": f"Bearer {args.key}"}
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=60) as http:
        resp = await http.post(
            "/api/v1/codebase/refresh",
            json={"commit_sha": commit_sha, "changes": changes},
        )
        if resp.status_code != 200:
            print(f"refresh 失败：{resp.status_code} {resp.text}", file=sys.stderr)
            return 1
        r = resp.json()
        print(f"\n入站 ✓ 新增 {r['modules_accepted']}，重复 {r['modules_duplicate']}")
        print("变更已进收件箱，下次 flush 时按模块重生成知识卡；之后运行 `ydm-distill sync` 更新 md。")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="ydm-distill", description="codebase 蒸馏客户端")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="dry-run：报有效文件数与预估 token，不调 LLM")
    p_scan.add_argument("repo")

    def add_remote(p):
        p.add_argument("repo")
        p.add_argument("--url", default="http://localhost:8000")
        p.add_argument("--key", required=True, help="space_key")

    p_run = sub.add_parser("run", help="全量蒸馏 + 上传知识卡 + 落盘 md")
    add_remote(p_run)
    p_run.add_argument("--model", default=None)

    p_sync = sub.add_parser("sync", help="从知识卡重建 md 投影")
    add_remote(p_sync)
    p_sync.add_argument("--all", action="store_true", help="重建全部，而非仅 sync_pending")

    p_refresh = sub.add_parser("refresh", help="增量：推 git 变更进收件箱（V1.5b）")
    add_remote(p_refresh)
    p_refresh.add_argument("--since", default="HEAD~1", help="diff 基线（默认 HEAD~1）")

    args = parser.parse_args()
    if args.cmd == "scan":
        sys.exit(cmd_scan(args))
    elif args.cmd == "run":
        sys.exit(asyncio.run(_run(args)))
    elif args.cmd == "refresh":
        sys.exit(asyncio.run(_refresh(args)))
    else:
        sys.exit(asyncio.run(_sync(args)))
