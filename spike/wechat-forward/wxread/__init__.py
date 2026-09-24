"""wxread — 可复用的 macOS 微信 4.x 数据读取层.

能力:
  - 定位微信数据目录 / 当前登录账号 / db_storage
  - Frida hook CommonCrypto 提取 SQLCipher 数据库密钥（attach 或 spawn）
  - 从 kvcomm 派生图片 AES-128 密钥与 XOR key
  - 解密 SQLCipher 分页数据库为明文 SQLite
  - 解码微信 V2 图片容器（_t 缩略图 JPEG / 原图 wxgf）
  - 高层接口：按群导出「文本消息 + 图片」结构化数据集

仅支持 macOS WeChat 4.x（在 4.1.13 验证）。全程本机读取，不外传数据。
"""
from .paths import find_account_root, find_db_storage, list_encrypted_dbs
from .keyextract import extract_db_keys, derive_image_key
from .decrypt import decrypt_database
from .imagedecode import decode_v2_image
from .reader import WeChatReader

__all__ = [
    "find_account_root",
    "find_db_storage",
    "list_encrypted_dbs",
    "extract_db_keys",
    "derive_image_key",
    "decrypt_database",
    "decode_v2_image",
    "WeChatReader",
]
