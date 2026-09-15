"""V1 rule-based reranker. V2 may swap in LLM cross-encoder or small model.

V2-2 热路分词改进：.split() 对中文返回整句一个 token（无效），
改用字符级切分——零依赖、对短文本（title/intent）token 重叠足够有效。
不引入 jieba（重依赖 + ~5MB 词典），也不上 LLM cross-encoder
（recall 是高频路径，LLM 延迟/成本不值；冷路 ts_rank 已是高质量排序）。
"""


_TOKEN_MIN_LEN = 1  # 单字 token 也保留（中文单字有语义）


def _tokenize(text: str) -> set[str]:
    """中文按字符、英文/数字按空格词切分（零依赖）。

    .split() 对中文整句返回单 token，重叠率恒为 0 或 1，排序失效；
    按字符切让 '发货单' 与 '发货单流转' 能算出真实重叠（3/5=0.6）。
    标点/空白不计入 token。
    """
    tokens: set[str] = set()
    for word in text.lower().split():
        if word.isascii():
            # 英文/数字词整体保留（保持英文词级重叠语义）
            if word.isalnum():
                tokens.add(word)
        else:
            # 含中文：按字符切，过滤标点
            tokens.update(ch for ch in word if ch.isalnum())
    return tokens


def rank_by_relevance(intent: str, memories: list) -> list:
    """Score each memory.

    - 冷路命中（带 `_ts_rank`）：weight×0.4 + ts_rank×0.6。
      P0-3 实测 ts_rank 排序质量远高于空格分词重叠——中文无空格，
      `.split()` 分词对中文几乎无效（意图是单 token）。
    - 热路 / ILIKE 兜底（无 rank）：weight×0.4 + 标题/内容 token 重叠。
      V2-2：token 重叠改用字符级切分，让中文重叠率真正生效。
    """
    intent_tokens = _tokenize(intent)

    for m in memories:
        ts_rank = getattr(m, "_ts_rank", None)
        if ts_rank is not None:
            m.score = getattr(m, "weight", 1.0) * 0.4 + float(ts_rank) * 0.6
            continue

        title_tokens = _tokenize(m.title or "")
        content_tokens = _tokenize(m.content or "")
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
