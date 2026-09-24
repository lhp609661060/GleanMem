"""高层读取接口：按群导出文本消息与图片数据集."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sqlite3
from glob import glob

from .decrypt import decrypt_database
from .imagedecode import decode_v2_image
from .paths import find_account_root, find_db_storage
from .keyextract import derive_image_key

_WX_PREFIX = re.compile(r"^(wxid_[A-Za-z0-9]+|\d+@openim):\n", re.S)


def _md5_table(chatroom: str) -> str:
    return "Msg_" + hashlib.md5(chatroom.encode()).hexdigest()


class WeChatReader:
    """读取指定账号的微信数据。

    参数:
      account_root: 账号根目录（缺省自动发现）
      db_keys:     {db相对路径: hex key}（由 extract_db_keys 得到）
      cache_dir:   解密后明文库缓存目录
    """

    def __init__(
        self,
        db_keys: dict[str, str],
        account_root: str | None = None,
        cache_dir: str | None = None,
        image_key: dict[str, object] | None = None,
    ) -> None:
        self.account_root = account_root or find_account_root()
        self.db_storage = find_db_storage(self.account_root)
        self.db_keys = db_keys
        self.cache_dir = cache_dir or os.path.join(
            self.account_root, ".wxread_plain"
        )
        os.makedirs(self.cache_dir, exist_ok=True)
        self._image_key = image_key

    # -- 解密缓存 -----------------------------------------------------------

    def _plain_path(self, rel: str) -> str:
        return os.path.join(self.cache_dir, rel.replace("/", "_"))

    def ensure_plain(self, rel: str) -> str:
        """解密指定库（相对 db_storage 路径）并返回明文库路径."""
        if rel not in self.db_keys:
            raise KeyError(f"缺少 {rel} 的密钥；请先 extract_db_keys 并打开相关会话")
        dst = self._plain_path(rel)
        if not os.path.exists(dst):
            decrypt_database(
                os.path.join(self.db_storage, rel),
                self.db_keys[rel],
                dst,
            )
        return dst

    # -- 定位群 -------------------------------------------------------------

    def find_chatroom(self, name_keyword: str) -> list[tuple[str, str]]:
        """按群名关键字返回 [(chatroom_id, 群名)]。"""
        contact_db = self.ensure_plain("contact/contact.db")
        con = sqlite3.connect(contact_db)
        rows = con.execute(
            "SELECT username, nick_name FROM contact "
            "WHERE local_type=2 AND (nick_name LIKE ? OR remark LIKE ?)",
            (f"%{name_keyword}%", f"%{name_keyword}%"),
        ).fetchall()
        return rows

    # -- 消息 ---------------------------------------------------------------

    def _locate_table(self, table: str) -> str | None:
        for shard in range(7):
            rel = f"message/message_{shard}.db"
            if rel not in self.db_keys:
                continue
            plain = self.ensure_plain(rel)
            con = sqlite3.connect(plain)
            hit = con.execute(
                "SELECT count(*) FROM sqlite_master WHERE name=?", (table,)
            ).fetchone()[0]
            if hit:
                return plain
        return None

    def export_messages(
        self,
        chatroom: str,
        sender_names: dict[str, str] | None = None,
    ) -> list[dict]:
        """导出一个群的干净消息（含文本与图片占位记录）。

        local_type: 1=文本, 3=图片。图片消息 text 通常为空，用 image 字段。
        """
        table = _md5_table(chatroom)
        plain = self._locate_table(table)
        if plain is None:
            raise FileNotFoundError(f"未找到消息表 {table}（密钥可能缺失）")

        if sender_names is None:
            ccon = sqlite3.connect(self.ensure_plain("contact/contact.db"))
            sender_names = {
                u: (n or u)
                for u, n in ccon.execute(
                    "SELECT username,nick_name FROM contact"
                )
            }

        con = sqlite3.connect(plain)
        out: list[dict] = []
        for local_id, ltype, ts, content in con.execute(
            f"SELECT local_id,local_type,create_time,message_content "
            f'FROM "{table}" ORDER BY create_time, local_id'
        ):
            if isinstance(content, bytes):
                content = content.decode("utf-8", "ignore")
            wxid = None
            body = content or ""
            m = _WX_PREFIX.match(body)
            if m:
                wxid = m.group(1)
                body = body[m.end():]
            elif ltype != 3 and body[:2] in ("(/", "(\\"):
                # 引用 / 系统控制块（protobuf 外壳，含随机字节），整体丢弃
                continue
            elif ltype != 1 and ltype != 3:
                # 非文本非图片（如表情、系统通知 10000）按需保留文本可读性
                if not re.search(r"[一-鿿A-Za-z0-9]{4,}", body):
                    continue
            when = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            item = {
                "id": local_id,
                "type": ltype,
                "time": when,
                "ts": ts,
                "sender_wxid": wxid,
                "sender": sender_names.get(wxid, wxid) if wxid else None,
                "text": body.strip() if ltype != 3 else body.strip(),
                "kind": "image" if ltype == 3 else "text",
            }
            out.append(item)
        return out

    # -- 图片 ---------------------------------------------------------------

    def image_key(self) -> dict[str, object]:
        if self._image_key is None:
            self._image_key = derive_image_key(self.account_root)
        return self._image_key

    def export_images(
        self,
        chatroom: str,
        months: tuple[str, ...] | None = None,
        decode: bool = True,
    ) -> list[dict]:
        """列出并解码一个群的图片（默认仅缩略图 _t）。

        返回 [{file, md5, month, thumb?, sha256?}]。decode=True 时解 V2，
        计算解码字节 sha256，供跨群内容对齐（转发图同哈希）。
        """
        folder_md5 = hashlib.md5(chatroom.encode()).hexdigest()
        attach = os.path.join(self.account_root, "msg", "attach", folder_md5)
        paths = glob(os.path.join(attach, "*", "Img", "*.dat"))
        if months:
            paths = [p for p in paths if any(f"/{m}/" in p for m in months)]

        ik = self.image_key() if decode else None
        out: list[dict] = []
        for p in sorted(paths):
            base = os.path.basename(p)
            is_thumb = base.endswith("_t.dat")
            md5 = base.replace("_t.dat", "").replace(".dat", "")
            month = p.split(os.sep)[-3]
            item = {"file": p, "md5": md5, "month": month, "is_thumb": is_thumb}
            if decode:
                raw = open(p, "rb").read()
                if raw[:4] == b"\x07\x08\x56\x32":
                    img = decode_v2_image(raw, str(ik["key_ascii"]), int(ik["xor_key"]))
                    item["sha256"] = hashlib.sha256(img).hexdigest()
                    item["magic"] = img[:4].decode("latin1", "ignore")
            out.append(item)
        return out

    # -- 数据集 -------------------------------------------------------------

    def export_dataset(
        self,
        chatroom: str,
        months: tuple[str, ...] | None = None,
    ) -> dict:
        msgs = self.export_messages(chatroom)
        imgs = self.export_images(chatroom, months=months, decode=True)
        return {"chatroom": chatroom, "messages": msgs, "images": imgs}

    @staticmethod
    def save_jsonl(rows: list[dict], path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
