"""md 投影渲染（D11）：知识卡 → <repo>/.gleanmem/wiki/*.md。

方向单一：卡 → md，永不反向。md 可重建 ⇒ 不参与审计不变式。
protected 保护：本地 md 首行含 `<!-- gleanmem: protected -->` 的文件不被覆盖。
"""

from __future__ import annotations

from pathlib import Path

PROTECTED_MARKER = "<!-- gleanmem: protected -->"
WIKI_SUBDIR = ".gleanmem/wiki"


def module_to_filename(module: str) -> str:
    safe = module.replace("/", "__").replace(" ", "_").strip(".") or "root"
    return f"{safe}.md"


def render_markdown(
    *,
    title: str,
    description: str,
    narrative: str,
    content: str,
    module: str,
    file_paths: list[str],
    commit_sha: str | None = None,
) -> str:
    """渲染叙述层 md（人读）。知识卡正文作为附录，便于人核对 Agent 看到的是什么。"""
    lines = [
        f"# {title}",
        "",
        f"> {description}",
        "",
        f"**模块**：`{module}`　**基线 commit**：`{commit_sha or '未记录'}`",
        "",
        "> 本文件由 gleanmem 从知识卡渲染生成（单向投影）。",
        f"> 手工修改请在本行上方加入 `{PROTECTED_MARKER}`，之后 sync 不再覆盖本文件。",
        "",
        "## 模块概述",
        "",
        narrative or "（无叙述层内容）",
        "",
        "## 知识卡正文（Agent 检索到的内容）",
        "",
        content or "（无）",
        "",
        "## 覆盖文件",
        "",
    ]
    lines += [f"- `{p}`" for p in file_paths] or ["（无）"]
    lines.append("")
    return "\n".join(lines)


def is_protected(path: Path) -> bool:
    """本地 md 是否被人标记保护（读前 5 行即可）。"""
    if not path.exists():
        return False
    try:
        with path.open(encoding="utf-8") as fh:
            for _ in range(5):
                line = fh.readline()
                if not line:
                    break
                if PROTECTED_MARKER in line:
                    return True
    except OSError:
        return False
    return False


def write_projection(repo_root: str | Path, module: str, markdown: str) -> tuple[Path, bool]:
    """落盘一个模块的 md。

    返回 (路径, 是否写入)。受保护文件返回 (路径, False)——人的修订优先。
    """
    target_dir = Path(repo_root) / WIKI_SUBDIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / module_to_filename(module)
    if is_protected(path):
        return path, False
    path.write_text(markdown, encoding="utf-8")
    return path, True
