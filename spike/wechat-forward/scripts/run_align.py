"""运行 A→B 对齐，输出转发对与统计。"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from wxread.align import align_text, align_images, pairs_to_dicts  # noqa: E402

OUT = os.path.join(ROOT, "output")


def load_msgs(g: str) -> list[dict]:
    return [json.loads(l) for l in open(os.path.join(OUT, f"group_{g}.jsonl"))]


def main() -> None:
    A = load_msgs("A")
    B = load_msgs("B")
    ai = json.load(open(os.path.join(OUT, "images_index_A.json")))
    bi = json.load(open(os.path.join(OUT, "images_index_B.json")))

    tp = align_text(A, B)
    ip = align_images(ai, bi, B)
    allpairs = tp + ip

    json.dump(
        pairs_to_dicts(allpairs),
        open(os.path.join(OUT, "forward_pairs.json"), "w"),
        ensure_ascii=False,
        indent=2,
    )

    print(f"文本转发对: {len(tp)}")
    print(f"图片转发对: {len(ip)}")
    print(f"合计转发: {len(allpairs)}")
    print("转发人分布(文本):", Counter(p.forwarder for p in tp).most_common())
    dts = [p.delay_s for p in tp if p.delay_s is not None]
    if dts:
        dts.sort()
        print(f"延迟(s): 中位 {dts[len(dts)//2]}, 90分位 {dts[int(len(dts)*0.9)]}, max {dts[-1]}")
    print(f"B群总消息 {len(B)}, 被识别为转发 {len(allpairs)}")


if __name__ == "__main__":
    main()
