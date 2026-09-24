"""转发预测 Agent：模拟真实 agent 行为并接入 GleanMem.

两种模式：
  - use_memory=True：先 recall 取历史案例与归纳，用“特征历史转发率”动态校准；
  - use_memory=False：仅固定规则（基线），不读记忆。
不使用 LLM（与 gleanmem 启发式主线一致），判定可解释。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .gleanmem_client import GleanMemClient

PLATE = re.compile(r"[京沪冀鲁豫津辽][A-Z][A-Z0-9]{4,}")
PHONE = re.compile(r"1[3-9]\d{9}")
SPEC = re.compile(r"\d{2,3}\s*[*x×]\s*\d+(?:\s*[*x×]\s*\d+)?")
KEYWORDS = ["可开单", "分货", "预分", "新增", "要吗", "有无", "急需",
            "提报", "增量", "派车", "整车", "没货", "改为"]


def features_of(text: str) -> list[str]:
    feats = []
    if PLATE.search(text): feats.append("plate")
    if PHONE.search(text): feats.append("phone")
    if SPEC.search(text): feats.append("spec")
    if any(k in text for k in KEYWORDS): feats.append("keyword")
    if len(text) >= 8 and re.search(r"\d", text): feats.append("numeric")
    return feats


@dataclass
class Prediction:
    a_id: int
    time: str
    sender: str | None
    will_forward: bool
    score: float
    text: str
    recalled: int
    reasons: list[str]


# 固定规则权重（基线）
BASE_W = {"plate": 0.34, "phone": 0.30, "spec": 0.30,
          "keyword": 0.24, "numeric": 0.08}


class ForwardAgent:
    def __init__(self, mem: GleanMemClient) -> None:
        self.mem = mem

    def _base_score(self, feats: list[str]) -> float:
        return min(sum(BASE_W.get(f, 0) for f in feats), 1.0)

    def _memory_stats(self, recalled: list[dict]) -> dict[str, list[int]]:
        """从召回文本中解析训练案例统计。

        记忆反馈形如：训练样例：A群消息「...」实际被转发/未被转发到B群。
        无法还原特征时，退化为只用总先验。
        """
        return {"total": [0, 0]}

    def predict(self, msg: dict, use_memory: bool = True) -> Prediction:
        text = msg["text"]
        feats = features_of(text)
        base = self._base_score(feats)
        reasons = [f"规则:{f}" for f in feats]
        recalled_n = 0

        score = base
        if use_memory:
            res = self.mem.recall(f"这条消息会被转发吗 {text[:60]}")
            mems = res.get("memories", [])
            recalled_n = len(mems)
            joined = "\n".join(m.get("summary", "") for m in mems)
            # 只统计“与当前消息特征重合”的召回案例
            fset = set(feats)
            pos = neg = 0
            for block in joined.split("\n"):
                if "转发训练样例" not in block:
                    continue
                mfeat = re.search(r"特征\[([^\]]*)\]", block)
                cfs = set(mfeat.group(1).split(",")) if mfeat else set()
                strong = {"plate","phone","spec","keyword"}
                cur_s = fset & strong
                can_s = cfs & strong
                # 至少一个共有强特征；无强特征的消息要求特征集合相等
                if cur_s:
                    if not (cur_s & can_s):
                        continue
                elif fset != cfs:
                    continue
                if "实际被转发" in block: pos += 1
                elif "实际未被转发" in block: neg += 1
            if pos + neg > 0:
                prior = pos / (pos + neg)
                score = 0.45 * base + 0.55 * prior
                reasons.append(f"同类记忆先验:{prior:.2f}({pos}+/{neg}-)")
            elif "车辆" in joined or "可开单" in joined:
                score = min(base + 0.05, 1.0)
                reasons.append("归纳规律确认")

        return Prediction(
            a_id=msg["id"], time=msg["time"], sender=msg["sender"],
            will_forward=score >= 0.5, score=round(score, 3),
            text=text, recalled=recalled_n, reasons=reasons,
        )
