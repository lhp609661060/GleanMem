"""时间切片回测（转发活跃期）：验证基于记忆的转发预测可靠性.

幂等设计：每次运行都自动 provision 一个全新 Space（同名旧 Space 先归档），
灌入初始规律 → 训练（逐例反馈入记忆）→ 测试 → 结束自动归档。
因此**任意次重复执行，结果一致**，不会累积旧案例。

--keep 时结束不归档，便于到前端页面查看该 Space 的记忆。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from wxread.agent import ForwardAgent  # noqa: E402
from wxread.gleanmem_client import GleanMemAdmin, GleanMemClient  # noqa: E402

OUT = os.path.join(ROOT, "output")
BASE_URL = "http://127.0.0.1:8000"
SPACE_NAME = "转发回测-临时"
TRAIN_N = 200
TEST_WIN = 150

INIT_FACTS = [
    "任务背景：监控A群磐金-建翔业务沟通→B群【内部】磐金协议分享的转发，预测A哪些消息会转发到B。",
    "转发人规律：主要转发人是「加一」，建发销售付龙、张有为、刘茂武偶发。",
    "转发内容规律：多为可直接执行的业务信息——车牌电话、分货规格可开单、新增吨位；基本不改写正文，闲聊不转发。",
]


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


def provision_space(admin: GleanMemAdmin) -> str:
    # 归档同名旧 Space（保证干净）
    for s in admin.list_spaces():
        if s.get("name") == SPACE_NAME and s.get("status") == "active":
            admin.archive_space(s["agent_id"])
            print(f"已归档旧 Space {s['agent_id'][:8]}")
    res = admin.create_space(SPACE_NAME, "回测临时空间，结束自动归档")
    return res["space_key"]


def main():
    keep = "--keep" in sys.argv

    A = sorted(
        [json.loads(l) for l in open(os.path.join(OUT, "group_A.jsonl"))],
        key=lambda m: m["ts"],
    )
    A = [m for m in A if m["kind"] == "text"]
    pairs = json.load(open(os.path.join(OUT, "forward_pairs.json")))
    labels = {p["a_id"] for p in pairs if p["kind"] == "text" and p["a_id"]}

    train = A[:TRAIN_N]
    test = A[TRAIN_N:TRAIN_N + TEST_WIN]
    print(f"训练 {len(train)}（真实转发 {sum(m['id'] in labels for m in train)}），"
          f"测试 {len(test)}（真实转发 {sum(m['id'] in labels for m in test)}）")

    admin_key = json.load(open(os.path.join(OUT, "admin_key.json")))["admin_key"]
    admin = GleanMemAdmin(BASE_URL, admin_key)
    space_key = provision_space(admin)
    space_id = None  # 归档需要
    for s in admin.list_spaces():
        if s.get("name") == SPACE_NAME:
            space_id = s["agent_id"]

    mem = GleanMemClient(BASE_URL, space_key)
    for fact in INIT_FACTS:
        mem.memorize(fact, source="example", marked_type="fact")
    mem.flush()
    agent = ForwardAgent(mem)

    # 训练期：recall → 预测 → 真实结果反馈入记忆
    for i, m in enumerate(train):
        agent.predict(m, use_memory=True)
        mem.memorize(feedback_text(m, m["id"] in labels),
                     source="example", marked_type="case")
        if (i + 1) % 50 == 0:
            mem.flush()
    mem.flush()

    # 测试期：有记忆 vs 无记忆
    results = {}
    for use_mem in (True, False):
        tp = fp = fn = tn = 0
        detail = []
        for m in test:
            pred = agent.predict(m, use_memory=use_mem)
            actual = m["id"] in labels
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

    if keep:
        print(f"\n保留 Space「{SPACE_NAME}」，key: {space_key}")
    else:
        admin.archive_space(space_id)
        print(f"\n已归档临时 Space，环境干净（--keep 可保留查看）")


if __name__ == "__main__":
    main()
