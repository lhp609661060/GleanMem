"""时间切片回测（转发活跃期）：验证基于记忆的转发预测可靠性."""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from wxread.agent import ForwardAgent  # noqa: E402
from wxread.gleanmem_client import GleanMemClient  # noqa: E402

OUT = os.path.join(ROOT, "output")
TRAIN_N = 200
TEST_WIN = 150


def prf(tp, fp, fn, tn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    acc = (tp + tn) / (tp + fp + fn + tn) if (tp + fp + fn + tn) else 0.0
    return p, r, f1, acc


def feedback_text(m, actual):
    from wxread.agent import features_of
    lab = "被转发" if actual else "未被转发"
    feats = ",".join(features_of(m["text"]))
    return f"转发训练样例 特征[{feats}]：A群消息「{m['text'][:30]}」实际{lab}到B群。"


def main():
    A = sorted(
        [json.loads(l) for l in open(os.path.join(OUT, "group_A.jsonl"))],
        key=lambda m: m["ts"],
    )
    A = [m for m in A if m["kind"] == "text"]
    pairs = json.load(open(os.path.join(OUT, "forward_pairs.json")))
    labels = {p["a_id"]: True for p in pairs if p["kind"] == "text" and p["a_id"]}

    train = A[:TRAIN_N]
    test = A[TRAIN_N:TRAIN_N + TEST_WIN]
    print(f"训练 {len(train)}（真实转发 {sum(labels.get(m['id'],0) for m in train)}），"
          f"测试 {len(test)}（真实转发 {sum(labels.get(m['id'],0) for m in test)}）")

    key = json.load(open(os.path.join(OUT, "space_key_clean.json")))["space_key"]
    mem = GleanMemClient("http://127.0.0.1:8000", key)
    agent = ForwardAgent(mem)

    for i, m in enumerate(train):
        agent.predict(m, use_memory=True)
        mem.memorize(feedback_text(m, labels.get(m["id"], False)),
                     source="example", marked_type="case")
        if (i + 1) % 50 == 0:
            mem.flush()
    mem.flush()
    print("训练期反馈已写入并 flush")

    results = {}
    for use_mem in (True, False):
        tp = fp = fn = tn = 0
        detail = []
        for m in test:
            pred = agent.predict(m, use_memory=use_mem)
            actual = labels.get(m["id"], False)
            if pred.will_forward and actual: tp += 1
            elif pred.will_forward: fp += 1
            elif actual: fn += 1
            else: tn += 1
            detail.append({"id": m["id"], "pred": pred.will_forward,
                           "actual": actual, "score": pred.score,
                           "reasons": pred.reasons})
        results["memory" if use_mem else "baseline"] = (prf(tp, fp, fn, tn),
                                                        (tp, fp, fn, tn), detail)

    for tag, ((p, r, f1, acc), cm, detail) in results.items():
        name = "有记忆" if tag == "memory" else "无记忆基线"
        print(f"\n[{name}] TP/FP/FN/TN={cm}")
        print(f"  precision={p:.3f} recall={r:.3f} F1={f1:.3f} accuracy={acc:.3f}")
        if tag == "memory":
            json.dump(detail, open(os.path.join(OUT, "backtest_detail.json"), "w"),
                      ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
