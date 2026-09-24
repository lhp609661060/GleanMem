"""路径发现：定位 macOS 微信 4.x 数据目录."""
from __future__ import annotations

import os
from glob import glob

# 微信容器根
WX_CONTAINER = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)
# 账号目录形如 <wxid>_<4位hex>
# db_storage 在其下


def find_account_root(wxid_hint: str | None = None) -> str:
    """返回账号根目录 .../xwechat_files/<wxid>_<hex>."""
    roots = [p for p in glob(os.path.join(WX_CONTAINER, "*")) if os.path.isdir(p)]
    # 只保留含 db_storage 的
    roots = [p for p in roots if os.path.isdir(os.path.join(p, "db_storage"))]
    if not roots:
        raise FileNotFoundError(f"未找到含 db_storage 的微信账号目录: {WX_CONTAINER}")
    if wxid_hint:
        hit = [p for p in roots if os.path.basename(p).startswith(wxid_hint)]
        if hit:
            return hit[0]
        raise FileNotFoundError(f"未找到匹配 wxid={wxid_hint} 的账号目录")
    if len(roots) == 1:
        return roots[0]
    # 多个：取最近修改的
    roots.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return roots[0]


def find_db_storage(account_root: str | None = None) -> str:
    account_root = account_root or find_account_root()
    db = os.path.join(account_root, "db_storage")
    if not os.path.isdir(db):
        raise FileNotFoundError(db)
    return db


def list_encrypted_dbs(db_storage: str | None = None) -> dict[str, bytes]:
    """返回 {相对路径: 首页 4096 字节}，仅含加密库（首 5 字节非 'SQLit'）."""
    db_storage = db_storage or find_db_storage()
    out: dict[str, bytes] = {}
    for root, _dirs, files in os.walk(db_storage):
        for fn in files:
            if not fn.endswith(".db"):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, "rb") as f:
                    page = f.read(4096)
            except OSError:
                continue
            if len(page) >= 4096 and page[:5] != b"SQLit":
                rel = os.path.relpath(path, db_storage)
                out[rel] = page
    return out


def msg_attach_root(account_root: str) -> str:
    """图片附件根 .../msg/attach."""
    return os.path.join(account_root, "msg", "attach")
