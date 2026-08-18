"""V1 rule-based reranker. V2 may swap in LLM cross-encoder or small model."""


def rank_by_relevance(intent: str, memories: list) -> list:
    """Score each memory.

    - 冷路命中（带 `_ts_rank`）：weight×0.4 + ts_rank×0.6。
      P0-3 实测 ts_rank 排序质量远高于空格分词重叠——中文无空格，
      `.split()` 分词对中文几乎无效（意图是单 token）。
    - 热路 / ILIKE 兜底（无 rank）：weight×0.4 + 标题/内容 token 重叠。
    """
    intent_tokens = set(intent.lower().split())

    for m in memories:
        ts_rank = getattr(m, "_ts_rank", None)
        if ts_rank is not None:
            m.score = getattr(m, "weight", 1.0) * 0.4 + float(ts_rank) * 0.6
            continue

        title_tokens = set((m.title or "").lower().split())
        content_tokens = set((m.content or "").lower().split())
        title_overlap = (
            len(intent_tokens & title_tokens) / max(len(intent_tokens), 1)
        )
        content_overlap = (
            len(intent_tokens & content_tokens) / max(len(intent_tokens), 1)
        )
        m.score = (
            getattr(m, "weight", 1.0) * 0.4
            + title_overlap * 0.4
            + content_overlap * 0.2
        )

    return sorted(memories, key=lambda x: getattr(x, "score", 0), reverse=True)
