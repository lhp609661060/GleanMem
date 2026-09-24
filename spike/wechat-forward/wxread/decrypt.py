"""解密 SQLCipher 4 分页数据库为明文 SQLite."""
from __future__ import annotations

import os

PAGE = 4096
RESERVE = 80
IV_OFF = PAGE - RESERVE  # 4016


def decrypt_database(
    enc_path: str,
    key_hex: str,
    dst_path: str | None = None,
) -> bytes:
    """解密单个 SQLCipher 库。

    布局：
      - 第 1 页：[16 salt][4000 加密字节][80 reserve]，逻辑首16字节为明文 magic。
      - 其余页：[4016 加密字节][80 reserve]（整页加密）。
    """
    from Crypto.Cipher import AES

    key = bytes.fromhex(key_hex)
    with open(enc_path, "rb") as f:
        data = f.read()

    n = len(data) // PAGE
    out = bytearray()
    for i in range(n):
        page = data[i * PAGE:(i + 1) * PAGE]
        iv = page[IV_OFF:IV_OFF + 16]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        if i == 0:
            dec = cipher.decrypt(page[16:IV_OFF])          # 4000
            out += b"SQLite format 3\x00" + dec           # 4016
        else:
            out += cipher.decrypt(page[0:IV_OFF])         # 4016
        out += b"\x00" * RESERVE

    raw = bytes(out)
    if dst_path:
        os.makedirs(os.path.dirname(os.path.abspath(dst_path)), exist_ok=True)
        with open(dst_path, "wb") as f:
            f.write(raw)
    return raw
