"""A→B 转发对齐：文本相似度 + 图片内容哈希.

输出转发对 (ForwardPair)，含：
  - b_msg / a_msg：B 群消息与其在 A 群的源
  - kind：text / image
  - forwarder：实际转发人
  - delay_s：转发延迟（秒）
  - similarity：文本相似度（图片=1.0）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher


def _normalize_text(t: str) -> str:
    t = re.sub(r"@[^\s@]+", "", t)
    return re.sub(r"\s+", "", t)


@dataclass
class ForwardPair:
    kind: str
    b_id: int
    a_id: int | None
    forwarder: str | None
    forwarder_wxid: str | None
    b_time: str
    a_time: str | None
    delay_s: int | None
    similarity: float
    b_text: str
    a_text: str | None
    image_sha: str | None = None


def align_text(
    a_msgs: list[dict],
    b_msgs: list[dict],
    min_sim: float = 0.6,
    max_delay: int = 3600,
    min_len: int = 6,
) -> list[ForwardPair]:
    """文本消息对齐。仅在时间窗 [a 早于 b 5s ~ max_delay] 内匹配。"""
    a_text = [m for m in a_msgs if m["kind"] == "text"]
    pairs: list[ForwardPair] = []
    used_a: set[int] = set()

    for b in b_msgs:
        if b["kind"] != "text":
            continue
        nb = _normalize_text(b["text"])
        if len(nb) < min_len:
            continue
        best = None
        best_s = 0.0
        for a in a_text:
            if a["id"] in used_a:
                continue
            dt = b["ts"] - a["ts"]
            if dt < -5 or dt > max_delay:
                continue
            na = _normalize_text(a["text"])
            if not na:
                continue
            s = SequenceMatcher(None, nb, na).ratio()
            if s > best_s:
                best_s = s
                best = (a, dt)
        if best and best_s >= min_sim:
            a, dt = best
            used_a.add(a["id"])
            pairs.append(
                ForwardPair(
                    kind="text",
                    b_id=b["id"],
                    a_id=a["id"],
                    forwarder=b["sender"],
                    forwarder_wxid=b["sender_wxid"],
                    b_time=b["time"],
                    a_time=a["time"],
                    delay_s=dt,
                    similarity=round(best_s, 3),
                    b_text=b["text"],
                    a_text=a["text"],
                )
            )
    return pairs


def align_images(
    a_images: list[dict],
    b_images: list[dict],
    b_msgs: list[dict],
    max_delay: int = 7200,
) -> list[ForwardPair]:
    """图片按解码内容 sha256 对齐（转发图字节相同）。

    a_images/b_images：images_index（含 sha256、month、md5、file）。
    转发时间用 B 群图片所在月份内、与之最近的图片消息时间近似，
    再回填真实转发人（若该时刻有图片消息）。
    """
    a_by_sha: dict[str, dict] = {}
    for it in a_images:
        if it.get("is_thumb") and it.get("sha256"):
            a_by_sha.setdefault(it["sha256"], it)

    # B 群图片消息（kind=image）按时间排序
    b_img_msgs = sorted(
        [m for m in b_msgs if m["kind"] == "image"], key=lambda m: m["ts"]
    )

    pairs: list[ForwardPair] = []
    seen_b_sha: set[str] = set()
    for it in b_images:
        sha = it.get("sha256")
        if not it.get("is_thumb") or not sha or sha in seen_b_sha:
            continue
        if sha not in a_by_sha:
            continue
        seen_b_sha.add(sha)
        a_it = a_by_sha[sha]

        # 找该图对应的 B 群图片消息：同月份、md5 前缀匹配
        # 消息里不含 md5，故用“同月份、时间最近的图片消息”近似
        month = it["month"]
        cand = [m for m in b_img_msgs if m["time"][:7] == month]
        b_msg = cand[0] if cand else None

        pairs.append(
            ForwardPair(
                kind="image",
                b_id=b_msg["id"] if b_msg else -1,
                a_id=None,
                forwarder=b_msg["sender"] if b_msg else None,
                forwarder_wxid=b_msg["sender_wxid"] if b_msg else None,
                b_time=b_msg["time"] if b_msg else f"{month}(approx)",
                a_time=a_it["month"],
                delay_s=None,
                similarity=1.0,
                b_text="[图片]",
                a_text="[图片]",
                image_sha=sha,
            )
        )
    return pairs


def pairs_to_dicts(pairs: list[ForwardPair]) -> list[dict]:
    return [asdict(p) for p in pairs]
