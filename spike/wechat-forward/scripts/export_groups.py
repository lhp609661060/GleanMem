"""一键导出 A/B 群文本+图片数据集。

用法:
  python scripts/export_groups.py                 # 用 output/db_keys.json
数据落到 output/group_A.jsonl / group_B.jsonl / images_index_*.json
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from wxread import WeChatReader  # noqa: E402

OUT = os.path.join(ROOT, "output")
os.makedirs(OUT, exist_ok=True)

GROUPS = {
    "A": "53423449835@chatroom",   # 磐金-建翔业务沟通群
    "B": "53531139660@chatroom",   # 【内部】磐金协议分享
}
MONTHS = ("2026-05", "2026-06")


def main() -> None:
    keys = json.load(open(os.path.join(OUT, "db_keys.json")))
    rd = WeChatReader(keys, cache_dir=os.path.join(OUT, "plain"))

    # 共用发送人名表
    rd.ensure_plain("contact/contact.db")

    for label, room in GROUPS.items():
        msgs = rd.export_messages(room)
        imgs = rd.export_images(room, months=MONTHS, decode=True)
        rd.save_jsonl(msgs, os.path.join(OUT, f"group_{label}.jsonl"))
        json.dump(
            imgs,
            open(os.path.join(OUT, f"images_index_{label}.json"), "w"),
            ensure_ascii=False,
            indent=2,
        )
        text_n = sum(1 for m in msgs if m["kind"] == "text")
        img_n = sum(1 for m in msgs if m["kind"] == "image")
        print(f"群{label} {room}: 文本 {text_n}, 图片消息 {img_n}, 图片文件 {len(imgs)}")


if __name__ == "__main__":
    main()
