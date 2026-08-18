"""codebase 蒸馏：仓库扫描 + 体积治理 + dry-run 预估（D14）。

纪律（01-design §代码库蒸馏）：
- 蒸馏器的**第一个**功能是「按排除清单过滤 + 报出有效文件数与预估 token」，
  否则一次全量就把演示预算烧光（07-architect-review §5.4）。
- 客户端侧执行：读本地代码的是 CLI/脚本，服务端零仓库访问权（D11）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

# 默认排除：vendored 依赖与构建产物（本仓库 5839 文件里大部分是这些，是现成反面教材）
DEFAULT_EXCLUDE_DIRS = frozenset(
    {
        ".git", ".venv", "venv", "node_modules", "site-packages", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", "target",
        ".next", ".nuxt", "coverage", ".tox", ".idea", ".vscode", ".yd-memory",
    }
)

# 只蒸馏源码：其余（锁文件、二进制、资源）无认知价值且吃 token
SOURCE_SUFFIXES = frozenset(
    {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go", ".rs", ".java", ".kt",
        ".rb", ".php", ".cs", ".c", ".h", ".cpp", ".hpp", ".swift", ".scala",
        ".sql", ".sh", ".yml", ".yaml", ".toml", ".md",
    }
)

EXCLUDE_FILENAMES = frozenset(
    {"uv.lock", "poetry.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock"}
)

MAX_FILE_BYTES = 200 * 1024  # 单文件超 200KB 视为生成物/数据文件，排除

# token 预估：1 token ≈ 3 字节（中英混合源码的保守估计）；蒸馏 prompt 另加固定开销
BYTES_PER_TOKEN = 3
PROMPT_OVERHEAD_TOKENS_PER_MODULE = 400


@dataclass
class ScannedFile:
    path: str  # 仓库相对路径
    fingerprint: str  # sha256:...（增量 diff 的比较基准，V1.5b 用）
    size: int


@dataclass
class ScannedModule:
    """模块 = 一个目录（V1.5a 简化版：目录即模块边界）。"""

    module: str
    files: list[ScannedFile] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


@dataclass
class ScanReport:
    """dry-run 产物：先报后跑（D14）。"""

    repo_path: str
    modules: list[ScannedModule]
    files_scanned: int
    files_excluded: int
    bytes_scanned: int

    @property
    def estimated_tokens(self) -> int:
        return (
            self.bytes_scanned // BYTES_PER_TOKEN
            + len(self.modules) * PROMPT_OVERHEAD_TOKENS_PER_MODULE
        )

    def summary(self) -> str:
        return (
            f"repo={self.repo_path}\n"
            f"有效文件 {self.files_scanned} 个（排除 {self.files_excluded} 个）\n"
            f"模块 {len(self.modules)} 个，源码 {self.bytes_scanned / 1024:.1f} KB\n"
            f"预估 token ≈ {self.estimated_tokens:,}"
        )


def fingerprint(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def scan_repo(
    repo_path: str | Path,
    *,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    include_suffixes: frozenset[str] = SOURCE_SUFFIXES,
) -> ScanReport:
    """扫描仓库，按排除清单过滤，按目录聚合成模块。不调用 LLM。"""
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise ValueError(f"不是目录: {root}")

    by_module: dict[str, ScannedModule] = {}
    excluded = 0
    total_bytes = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        # 排除目录（任意一级命中即排除）
        if any(part in exclude_dirs for part in rel.parts[:-1]):
            excluded += 1
            continue
        if path.name in EXCLUDE_FILENAMES or path.suffix not in include_suffixes:
            excluded += 1
            continue
        try:
            data = path.read_bytes()
        except OSError:
            excluded += 1
            continue
        if len(data) > MAX_FILE_BYTES or not data:
            excluded += 1
            continue

        module = str(rel.parent) if str(rel.parent) != "." else "(root)"
        by_module.setdefault(module, ScannedModule(module=module)).files.append(
            ScannedFile(path=str(rel), fingerprint=fingerprint(data), size=len(data))
        )
        total_bytes += len(data)

    modules = sorted(by_module.values(), key=lambda m: m.module)
    return ScanReport(
        repo_path=str(root),
        modules=modules,
        files_scanned=sum(len(m.files) for m in modules),
        files_excluded=excluded,
        bytes_scanned=total_bytes,
    )
