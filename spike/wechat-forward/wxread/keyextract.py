"""提取 SQLCipher 数据库密钥与图片密钥.

DB 密钥：Frida hook libcommonCrypto 的 CCCryptor 系列，把所有疑似 32 字节
buffer 与每个库的第 1 页做 HMAC 校验，命中即确定该库 key。

图片密钥：从 kvcomm 的 key_<code>_* 文件派生（等价 wxkey image-key）。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import struct
import time
from glob import glob

from .paths import find_account_root, list_encrypted_dbs

# ---------------------------------------------------------------------------
# Page-1 HMAC 校验（SQLCipher 4）
# ---------------------------------------------------------------------------

def verify_db_key(key: bytes, page1: bytes) -> bool:
    """用第 1 页的 HMAC 校验候选 32 字节 key."""
    salt = page1[:16]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, 32)
    expected = hmac.new(
        mac_key,
        page1[16:4032] + struct.pack("<I", 1),
        hashlib.sha512,
    ).digest()
    return hmac.compare_digest(expected, page1[4032:4096])


# Frida 脚本：对 cryptor 创建函数的每个指针参数读取 64 字节，
# 发送其中全部 32 字节滑动窗口。
_HOOK_JS = r"""
var lib='libcommonCrypto.dylib';
function allWindows(ptr,span){
  var res=[];
  try{
    var raw=ptr.readByteArray(span); if(!raw)return res;
    var b=new Uint8Array(raw);
    for(var i=0;i+32<=b.length;i++){
      var h=''; for(var j=0;j<32;j++) h+=("0"+b[i+j].toString(16)).slice(-2);
      res.push(h);
    }
  }catch(e){}
  return res;
}
['CCCryptorCreate','CCCryptorCreateWithMode','CCCrypt'].forEach(function(fn){
  var p=Module.findExportByName(lib,fn);
  if(!p){ return; }
  Interceptor.attach(p,{onEnter:function(a){
    for(var i=0;i<9;i++){
      try{
        var ws=allWindows(a[i],64);
        if(ws.length) send({tag:'w',ws:ws});
      }catch(e){}
    }
  }});
});
send({tag:'ready'});
"""


def _frida_session(attach_pid: bool = True, timeout: float = 100.0) -> dict[str, str]:
    """attach 到运行中的微信并收集 key。需用户打开会话触发解密。"""
    import frida
    import subprocess

    dbs = list_encrypted_dbs()
    matched: dict[str, str] = {}
    tested: set[str] = set()

    def consider(hexkey: str) -> None:
        if hexkey in tested or len(hexkey) != 64:
            return
        tested.add(hexkey)
        key = bytes.fromhex(hexkey)
        for rel, page in dbs.items():
            if rel not in matched and verify_db_key(key, page):
                matched[rel] = hexkey
                print(f"[wxread] FOUND {rel} {hexkey}", flush=True)

    def on_message(msg, _data):
        if msg.get("type") == "send":
            payload = msg["payload"]
            if isinstance(payload, dict) and payload.get("tag") == "w":
                for h in payload["ws"]:
                    consider(h)

    dev = frida.get_local_device()
    if attach_pid:
        pid = int(subprocess.check_output(["pgrep", "-x", "WeChat"]).strip())
        session = dev.attach(pid)
    else:
        pid = dev.spawn(["/Applications/WeChat.app/Contents/MacOS/WeChat"])
        session = dev.attach(pid)

    script = session.create_script(_HOOK_JS)
    script.on("message", on_message)
    script.load()

    if not attach_pid:
        dev.resume(pid)

    time.sleep(timeout)
    return matched


def extract_db_keys(
    attach_pid: bool = True,
    timeout: float = 100.0,
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """提取 {db相对路径: 64位hex key}。

    attach_pid=True：微信须在运行；调用后需打开/切换会话触发解密。
    attach_pid=False：spawn 冷启动，登录后打开会话（适合补齐缓存的库）。
    existing：已有 key（合并，不重复验证）。
    """
    found = _frida_session(attach_pid=attach_pid, timeout=timeout)
    if existing:
        merged = dict(existing)
        merged.update(found)
        return merged
    return found


# ---------------------------------------------------------------------------
# 图片密钥（kvcomm 派生，等价 wxkey image-key）
# ---------------------------------------------------------------------------

def _kvcomm_dirs(account_root: str) -> list[str]:
    # account_root = .../Documents/xwechat_files/<acct>
    xwechat_files = os.path.dirname(account_root)       # .../Documents/xwechat_files
    documents_root = os.path.dirname(xwechat_files)     # .../Documents
    container_data = os.path.dirname(documents_root)    # .../Data
    dirs = [
        os.path.join(documents_root, "app_data", "net", "kvcomm"),
        os.path.join(documents_root, "app_data", "ilink", "kvcomm"),
        os.path.join(documents_root, "xwechat", "net", "kvcomm"),
    ]
    for p in glob(
        os.path.join(documents_root, "app_data", "radium", "ilink", "*", "kvcomm")
    ):
        dirs.append(p)
    dirs += [
        os.path.join(
            container_data,
            "Library",
            "Caches",
            "com.tencent.xinWeChat",
            "2.0b4.0.9",
            "kvcomm",
        ),
        os.path.join(container_data, ".wxapplet", "ilink", "kvcomm"),
    ]
    return list(dict.fromkeys(dirs))


def _collect_codes(account_root: str) -> list[int]:
    codes: set[int] = set()
    for d in _kvcomm_dirs(account_root):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.startswith("key_"):
                continue
            rest = name[4:]
            code_text = rest.split("_", 1)[0]
            try:
                codes.add(int(code_text))
            except ValueError:
                continue
    return sorted(codes)


def _derive_from_code(code: int, wxid: str) -> str:
    """deriveImageKeyFromKVCode：md5(f"{code}{wxid}") 取前16字符（ASCII key）."""
    digest = hashlib.md5(f"{code}{wxid}".encode()).hexdigest()
    return digest[:16]


def derive_image_key(account_root: str | None = None) -> dict[str, object]:
    """派生图片密钥，返回 {key_ascii, xor_key}。

    key_ascii 是 16 字符 hex 串，其 ASCII 字节即 AES-128 key；
    xor_key 是 V2 容器尾部 XOR 单字节。
    """
    from Crypto.Cipher import AES  # 仅用于校验

    account_root = account_root or find_account_root()
    leaf = os.path.basename(account_root.rstrip("/"))
    wxid_candidates = [leaf]
    if leaf.startswith("wxid_"):
        # 目录形如 wxid_<id>_<4hex>，真实 wxid 去掉尾部 _<4hex>
        body = leaf[len("wxid_"):]
        parts = body.split("_")
        if len(parts) >= 2 and len(parts[-1]) == 4:
            wxid_candidates.append("wxid_" + "_".join(parts[:-1]))
        wxid_candidates.append("wxid_" + parts[0])
    # 去重保序
    wxid_candidates = list(dict.fromkeys(wxid_candidates))

    # 找一个 V2 缩略图作为校验模板
    attach = os.path.join(account_root, "msg", "attach")
    template: bytes | None = None
    # attach/<md5>/<YYYY-MM>/Img/<file>_t.dat
    for path in glob(os.path.join(attach, "*", "*", "Img", "*_t.dat")):
        try:
            data = open(path, "rb").read()
        except OSError:
            continue
        if len(data) >= 31 and data[:3] == b"\x07\x08V":
            template = data
            break
    if template is None:
        raise FileNotFoundError("未找到 V2 图片模板")
    encrypted = template[15:31]

    codes = _collect_codes(account_root)
    for wxid in wxid_candidates:
        for code in codes:
            keystr = _derive_from_code(code, wxid)
            aes_key = keystr.encode("ascii")
            try:
                cipher = AES.new(aes_key, AES.MODE_ECB)
            except ValueError:
                continue
            dec = cipher.decrypt(encrypted)
            if dec[:3] == b"\xff\xd8\xff" or dec[:4] == b"\x89PNG" or dec[:4] == b"wxgf":
                xor_key = code & 0xFF
                return {"key_ascii": keystr, "xor_key": xor_key, "code": code}
    raise RuntimeError("未能从 kvcomm 派生出可验证的图片密钥")
