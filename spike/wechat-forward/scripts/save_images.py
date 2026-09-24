"""把 A/B 群图片解码落盘为 JPG，供查看与转发对齐。

输出:
  output/images_A/<sha256前16>.jpg
  output/images_B/<sha256前16>.jpg
内容相同的图，sha256 相同 → 直接判定转发。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "output")


def save_group(label: str, key_ascii: str, xor_key: int) -> int:
    sys.path.insert(0, ROOT)
    from wxread.imagedecode import decode_v2_image

    items = json.load(open(os.path.join(OUT, f"images_index_{label}.json")))
    dest_dir = os.path.join(OUT, f"images_{label}")
    os.makedirs(dest_dir, exist_ok=True)

    n = 0
    seen: set[str] = set()
    for it in items:
        if not it.get("is_thumb") or it.get("magic") not in ("", None) and it.get("magic") != "\xff\xd8\xff":
            # 只解缩略图（JPEG）
            pass
        raw = open(it["file"], "rb").read()
        if raw[:4] != b"\x07\x08\x56\x32":
            continue
        img = decode_v2_image(raw, key_ascii, xor_key)
        if img[:3] != b"\xff\xd8\xff":
            continue
        sha = it["sha256"]
        if sha in seen:
            continue
        seen.add(sha)
        open(os.path.join(dest_dir, sha[:20] + ".jpg"), "wb").write(img)
        n += 1
    print(f"群{label}: 保存 {n} 张 JPG")
    return n


if __name__ == "__main__":
    # 图片 key（与 wxread.derive_image_key 一致）
    key_ascii = "a51b610b76d87a04"
    xor_key = 158
    save_group("A", key_ascii, xor_key)
    save_group("B", key_ascii, xor_key)
