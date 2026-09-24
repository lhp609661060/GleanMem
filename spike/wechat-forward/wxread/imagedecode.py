"""解码微信 Mac 4.x V2 图片容器.

容器布局（在 4.1.13 实测）:
  [0:4]  magic 07 08 56 32  ("\x07\x08V2")
  [4:6]  08 07
  [6:10] aes_size  uint32 LE   （AES-ECB 区长度，通常 1024）
  [10:14] xor_size uint32 LE   （尾部 XOR 区长度）
  [14]   padding/标志字节
  [15:]  body = aes_part + mid + xor_part

解码：
  aes_part 用 AES-128-ECB（key 为 16 字符 ASCII）解密；
  xor_part 与单字节 xor_key 异或；
  mid 原样保留。
缩略图 _t 解码后为 JPEG；原图解码后多为 wxgf（微信自研高清格式）。
"""
from __future__ import annotations

import struct

MAGIC = b"\x07\x08\x56\x32"


def decode_v2_image(
    data: bytes,
    key_ascii: str,
    xor_key: int,
) -> bytes:
    if data[:4] != MAGIC:
        # 非 V2：可能是旧的单字节 XOR dat，交给上层处理
        raise ValueError("not a V2 image container")
    aes_size = struct.unpack("<I", data[6:10])[0]
    xor_size = struct.unpack("<I", data[10:14])[0]
    body = data[15:]

    aes_part = body[:aes_size]
    rest = body[aes_size:]
    if xor_size <= len(rest):
        xor_part = rest[len(rest) - xor_size:]
        mid = rest[:len(rest) - xor_size]
    else:
        xor_part = b""
        mid = rest

    from Crypto.Cipher import AES

    cipher = AES.new(key_ascii.encode("ascii"), AES.MODE_ECB)
    pad = (16 - len(aes_part) % 16) % 16
    dec_aes = cipher.decrypt(aes_part + b"\x00" * pad)[:len(aes_part)]
    dec_xor = bytes(b ^ xor_key for b in xor_part)
    return dec_aes + mid + dec_xor
