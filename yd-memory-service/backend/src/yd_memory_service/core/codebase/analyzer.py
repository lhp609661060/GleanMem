"""CodebaseAnalyzer：模块级代码蒸馏（V1.5a batch）。

契约（01-design §代码库蒸馏，D14）：
- 单模块一次独立 LLM 调用，无跨模块上下文依赖 → 可并发、可断点续跑。
- 一次产出双层：知识卡（description ≤100 字，进 wiki_documents）+ 叙述层 md（人读）。
- token 硬预算由调用方（distiller）按 run 累计裁决，超限中止。
- 失败不静默：返回 None，由 distiller 记入 run 的 error 计数，不产半成品卡。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from yd_memory_service.config import settings

from .scanner import ScannedModule

logger = logging.getLogger(__name__)

DESCRIPTION_MAX_CHARS = 100  # Skill 机制约束：description 是 tsvector 匹配命脉

SYSTEM_PROMPT = """你是代码库蒸馏器。给定一个模块的源码，产出该模块的认知层知识，供 AI Agent 检索使用。

只返回 JSON 对象，不要解释、不要代码围栏：
{
  "title": "模块名 —— 一句话职责",
  "description": "模块职责一句话，≤100 字，中文，这是检索匹配的唯一依据，要包含模块的关键概念词",
  "content": "知识卡正文（Agent 读）：模块边界、对外接口、关键约定、调用关系、变更注意事项。用简洁要点，≤800 字",
  "narrative": "叙述层（人读）：这个模块是做什么的、怎么组织的、读代码时该从哪进入。可以用段落，≤600 字",
  "tags": ["≤5 个关键词"]
}

要求：用中文；只描述代码里**确实存在**的事实，不推测、不补全你认为应该有的设计；找不到足够信息时如实写"信息不足"。"""

# 单模块喂给 LLM 的源码上限（约 12k token），超出则截断并在 prompt 里说明
MODULE_SOURCE_MAX_CHARS = 36_000


@dataclass
class CardDraft:
    """蒸馏产物：一个模块 → 一张知识卡 + 一段叙述层。"""

    module: str
    title: str
    description: str
    content: str
    narrative: str
    tags: list[str]
    file_paths: list[str]
    fingerprints: dict[str, str]
    tokens_used: int = 0
    raw_response: str = ""


def build_module_source(module: ScannedModule, repo_root: str) -> str:
    """拼接模块内文件源码（带路径标注，超限截断）。"""
    from pathlib import Path

    parts: list[str] = []
    budget = MODULE_SOURCE_MAX_CHARS
    for f in module.files:
        if budget <= 0:
            parts.append(f"\n（余下 {len(module.files) - len(parts)} 个文件因长度限制未纳入）")
            break
        try:
            text = (Path(repo_root) / f.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > budget:
            text = text[:budget] + "\n…（截断）"
        parts.append(f"===== {f.path} =====\n{text}")
        budget -= len(text)
    return "\n\n".join(parts)


def _parse_card_json(raw: str) -> dict | None:
    """剥离围栏 + 解析；非 dict → None（同 learning.py 的解析纪律）。"""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        parsed = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM card JSON 解析失败，跳过该模块：%s", exc)
        return None
    return parsed if isinstance(parsed, dict) else None


class CodebaseAnalyzer:
    def __init__(self, *, model: str | None = None):
        self.model = model or settings.llm_model

    async def distill(self, module: ScannedModule, repo_root: str) -> CardDraft | None:
        """蒸馏单个模块。失败返回 None（不产半成品卡）。"""
        if not settings.llm_api_key:
            logger.warning("No LLM API key configured; codebase distillation skipped")
            return None

        import openai  # late import（同 learning.py）

        client = openai.AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
        )

        source = build_module_source(module, repo_root)
        if not source.strip():
            return None

        raw = ""
        tokens = 0
        try:
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"模块路径：{module.module}\n文件数：{len(module.files)}\n\n{source}",
                    },
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content or ""
            if resp.usage:
                tokens = resp.usage.total_tokens or 0
        except Exception:
            logger.exception("Codebase distillation failed for module %s", module.module)
            return None

        data = _parse_card_json(raw)
        if not data:
            logger.warning("Module %s: unparsable LLM response", module.module)
            return None

        description = (data.get("description") or "").strip()
        if not description:
            # description 是检索命脉，缺失即视为失败（Skill 机制不变式）
            logger.warning("Module %s: empty description, rejected", module.module)
            return None
        if len(description) > DESCRIPTION_MAX_CHARS:
            description = description[:DESCRIPTION_MAX_CHARS]

        tags = data.get("tags")
        if not isinstance(tags, list):
            tags = []

        return CardDraft(
            module=module.module,
            title=(data.get("title") or module.module).strip()[:500],
            description=description,
            content=(data.get("content") or "").strip(),
            narrative=(data.get("narrative") or "").strip(),
            tags=[str(t)[:50] for t in tags[:5]],
            file_paths=[f.path for f in module.files],
            fingerprints={f.path: f.fingerprint for f in module.files},
            tokens_used=tokens,
            raw_response=raw,
        )
