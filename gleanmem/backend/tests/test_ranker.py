"""N5：重排器——ts_rank 合并与中文退化问题。"""
from __future__ import annotations

from gleanmem.orchestrator.ranker import rank_by_relevance


class FakeMemory:
    def __init__(self, weight: float = 1.0, title: str = "", content: str = "", ts_rank: float | None = None):
        self.weight = weight
        self.title = title
        self.content = content
        self._ts_rank = ts_rank
        self.score = 0.0


def test_ts_rank_dominates_when_present():
    """冷路命中：score = weight×0.4 + ts_rank×0.6，rank 权重更高。"""
    m1 = FakeMemory(weight=0.9, ts_rank=0.5)   # 0.9×0.4+0.5×0.6 = 0.66
    m2 = FakeMemory(weight=0.5, ts_rank=0.9)   # 0.5×0.4+0.9×0.6 = 0.74
    ranked = rank_by_relevance("任意中文查询", [m1, m2])
    assert ranked[0] is m2


def test_fallback_orders_by_weight_plus_overlap():
    """无 ts_rank（热路/ILIKE）：英文 token 重叠 + 权重。"""
    m1 = FakeMemory(weight=1.0, title="hello world", content="x")
    m2 = FakeMemory(weight=0.6, title="hello world", content="x")
    ranked = rank_by_relevance("hello", [m2, m1])
    assert ranked[0] is m1


def test_chinese_intent_fallback_orders_by_weight():
    """中文意图在 fallback 路径是单 token（无空格），重叠≈0 → 主要按权重排序。"""
    m1 = FakeMemory(weight=0.9, title="发货单重量", content="吨")
    m2 = FakeMemory(weight=0.5, title="合同审批", content="用印")
    ranked = rank_by_relevance("发货单重量用什么单位", [m2, m1])
    assert ranked[0] is m1
