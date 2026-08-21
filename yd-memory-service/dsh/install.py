#!/usr/bin/env python3
"""一键把 yd-memory-service 接入 DSH。

可重复执行，幂等。做五件事：
1. 后端不在线时自动拉起 uvicorn（可用 --no-start-backend 关闭）
2. 确保存在一个 Agent Space，并把 space_key / agent_id 写入 backend/.env
3. 把 DSH skill（ydm-memory-client）复制到 DSH 技能库
4. 把 MCP 插件实例合并进 DSH profile 的 cordis.patch.yml（serverName=ydmemory）
5. 验证 MCP stdio 入口能被正常拉起并暴露 3 个工具

用法：
    ./dsh/install.py                    # 默认 web profile，自动起后端
    ./dsh/install.py --profile tui      # 其它 profile
    ./dsh/install.py --space-name 我的记忆空间
    ./dsh/install.py --no-start-backend # 后端已自己跑起来时用
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

MCP_ENTRY_ID = "mcp-ydmemory"
SKILL_NAME = "ydm-memory-client"


def log(msg: str) -> None:
    print(msg, flush=True)


def http_json(url: str, method: str = "GET", data: dict | None = None, timeout: float = 10, headers: dict | None = None) -> tuple[int, object]:
    body = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, {"detail": raw}
    except urllib.error.URLError as e:
        return 0, {"detail": str(e)}


def health_ok(base_url: str) -> bool:
    try:
        status, _ = http_json(base_url + "/health", timeout=3)
        return status == 200
    except Exception:
        return False


def start_backend(backend: Path, base_url: str) -> None:
    uv = shutil.which("uv")
    if not uv:
        sys.exit("未找到 uv，请先安装 uv 或手动启动后端后加 --no-start-backend 重跑。")
    port = base_url.rsplit(":", 1)[-1]
    log(f"[1/5] 后端不在线，自动启动：uv run uvicorn ... --port {port}")
    log_file = backend / ".install-backend.log"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(backend / "src")
    env.pop("__PYVENV_LAUNCHER__", None)
    with open(log_file, "ab") as f:
        subprocess.Popen(
            [uv, "run", "uvicorn", "yd_memory_service.main:app", "--host", "127.0.0.1", "--port", port],
            cwd=str(backend),
            env=env,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(40):
        if health_ok(base_url):
            log("    后端已就绪。")
            return
        time.sleep(0.5)
    sys.exit(f"后端启动超时，请查看日志：{log_file}")


def upsert_env(env_file: Path, key: str, value: str) -> None:
    lines: list[str] = []
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_env(env_file: Path, key: str) -> str | None:
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return None


def ensure_space(backend: Path, base_url: str, space_name: str, force_new: bool) -> tuple[str, str]:
    env_file = backend / ".env"
    space_key = read_env(env_file, "YDM_SPACE_KEY")
    agent_id = read_env(env_file, "YDM_AGENT_ID")

    if space_key and not force_new:
        status, payload = http_json(
            base_url + "/api/v1/spaces",
            timeout=5,
            headers={"Authorization": f"Bearer {space_key}"},
        )
        if status == 200 and isinstance(payload, list) and len(payload) == 1:
            resolved_agent_id = str(payload[0].get("agent_id") or agent_id or "")
            if resolved_agent_id:
                if resolved_agent_id != agent_id:
                    upsert_env(env_file, "YDM_AGENT_ID", resolved_agent_id)
                    agent_id = resolved_agent_id
                log(f"[2/5] 复用已有 Space：{agent_id}（key 校验通过）")
                return space_key, agent_id
        log("    已有 YDM_SPACE_KEY 校验失败，重新创建 Space。")

    log(f"[2/5] 创建 Space：{space_name}")
    status, payload = http_json(base_url + "/api/v1/spaces", method="POST", data={"name": space_name})
    if status not in (200, 201) or not isinstance(payload, dict) or "space_key" not in payload:
        sys.exit(f"创建 Space 失败：HTTP {status} {payload}")
    space_key = str(payload["space_key"])
    agent_id = str(payload["agent_id"])
    upsert_env(env_file, "YDM_SPACE_KEY", space_key)
    upsert_env(env_file, "YDM_AGENT_ID", agent_id)
    log(f"    已写入 {env_file}（YDM_SPACE_KEY / YDM_AGENT_ID）")
    return space_key, agent_id


def mcp_entry_text(backend: Path, agent_id: str) -> str:
    venv_python = backend / ".venv" / "bin" / "python"
    stdio_server = backend / "src" / "yd_memory_service" / "mcp" / "stdio_server.py"
    return f"""- id: {MCP_ENTRY_ID}
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: ydmemory
    transport: stdio
    command: /usr/bin/env
    args:
      - '-u'
      - __PYVENV_LAUNCHER__
      - {venv_python}
      - {stdio_server}
    cwd: {backend}
    env:
      PYTHONPATH: {backend / 'src'}
      YDM_AGENT_ID: {agent_id}
"""


def merge_patch_file(target: Path, entry_text: str) -> None:
    backup = None
    if target.exists():
        backup = target.with_name(target.name + ".bak-" + datetime.now().strftime("%Y%m%d%H%M%S"))
        shutil.copy2(target, backup)
        text = target.read_text(encoding="utf-8")
    else:
        text = "[]\n"

    lines = text.splitlines()
    new_lines: list[str] = []
    i = 0
    removed = False
    while i < len(lines):
        if lines[i].strip() == f"- id: {MCP_ENTRY_ID}":
            i += 1
            while i < len(lines) and not (lines[i].strip().startswith("- id:") or lines[i].strip().startswith("- name:")):
                i += 1
            removed = True
            continue
        new_lines.append(lines[i])
        i += 1
    text = "\n".join(new_lines)

    stripped = text.strip()
    if stripped in ("", "[]"):
        text = entry_text.rstrip("\n")
    else:
        text = text.rstrip("\n") + "\n" + entry_text.rstrip("\n")

    target.write_text(text + "\n", encoding="utf-8")
    log(f"    已写入 MCP 插件实例（id={MCP_ENTRY_ID}, serverName=ydmemory）")
    if backup:
        log(f"    原文件备份：{backup}")


def install_skill(repo: Path, dsh_home: Path, agents_home: Path) -> None:
    src = repo / "dsh" / "skills" / SKILL_NAME / "SKILL.md"
    if not src.exists():
        sys.exit(f"找不到技能源文件：{src}")
    destinations = [
        dsh_home / "skills" / SKILL_NAME / "SKILL.md",
        agents_home / "skills" / SKILL_NAME / "SKILL.md",
    ]
    for dest in destinations:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            log(f"    技能已安装：{dest}")
        except PermissionError as e:
            log(f"    [跳过] 无权限写入 {dest}：{e}")
        except OSError as e:
            log(f"    [跳过] 写入 {dest} 失败：{e}")


def verify_stdio(backend: Path, agent_id: str) -> bool:
    venv_python = backend / ".venv" / "bin" / "python"
    stdio_server = backend / "src" / "yd_memory_service" / "mcp" / "stdio_server.py"
    if not venv_python.exists() or not stdio_server.exists():
        log("[5/5] [跳过] 缺少 .venv 或 stdio_server.py，无法验证 MCP 入口。")
        return False
    env = dict(os.environ)
    env.pop("__PYVENV_LAUNCHER__", None)
    env["PYTHONPATH"] = str(backend / "src")
    env["YDM_AGENT_ID"] = agent_id
    try:
        proc = subprocess.Popen(
            [str(venv_python), str(stdio_server)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        log(f"[5/5] [跳过] 无法启动 MCP stdio 进程：{e}")
        return False

    import select

    def send(obj: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def recv(timeout: float = 15) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if ready:
                line = proc.stdout.readline().strip()  # type: ignore[union-attr]
                if line:
                    return json.loads(line)
        return None

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "ydm-installer", "version": "0"}}})
        if recv() is None:
            raise RuntimeError("initialize 无响应")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        resp = recv()
        tools = [t.get("name") for t in resp.get("result", {}).get("tools", [])] if resp else []
        if "recall" not in tools or "memorize" not in tools:
            raise RuntimeError(f"工具列表不完整：{tools}")
        log(f"[5/5] MCP stdio 验证通过，暴露工具：{', '.join(tools)}")
        return True
    except Exception as e:
        log(f"[5/5] [警告] MCP stdio 验证失败：{e}")
        return False
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description="一键接入 DSH")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--profile", default="web")
    parser.add_argument("--space-name", default="dsh-助手记忆空间")
    parser.add_argument("--dsh-home", default=str(Path.home() / ".dsh"))
    parser.add_argument("--agents-home", default=str(Path.home() / ".agents"))
    parser.add_argument("--force-new-space", action="store_true")
    parser.add_argument("--no-start-backend", action="store_true")
    parser.add_argument("--no-skill", action="store_true")
    parser.add_argument("--no-mcp", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    args = parser.parse_args()

    service_dir = Path(__file__).resolve().parent.parent
    backend = service_dir / "backend"
    if not (backend / "pyproject.toml").exists():
        sys.exit(f"找不到后端目录：{backend}")

    if not health_ok(args.base_url):
        if args.no_start_backend:
            sys.exit(f"后端不在线（{args.base_url}/health）。请先启动后端，或去掉 --no-start-backend 让脚本自动启动。")
        start_backend(backend, args.base_url)

    space_key, agent_id = ensure_space(backend, args.base_url, args.space_name, args.force_new_space)

    if not args.no_skill:
        log("[3/5] 安装 DSH skill ...")
        install_skill(service_dir, Path(args.dsh_home), Path(args.agents_home))
    else:
        log("[3/5] 跳过 skill 安装（--no-skill）")

    if not args.no_mcp:
        log("[4/5] 合并 MCP 插件实例到 DSH profile patch ...")
        target = Path(args.dsh_home) / "profiles" / args.profile / "cordis.patch.yml"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            merge_patch_file(target, mcp_entry_text(backend, agent_id))
        except PermissionError as e:
            log(f"    [失败] 无权限写入 {target}：{e}")
            log(f"    请手动把以下内容合并进 {target}：")
            print(mcp_entry_text(backend, agent_id))
    else:
        log("[4/5] 跳过 MCP 安装（--no-mcp）")

    if not args.no_verify:
        verify_stdio(backend, agent_id)
    else:
        log("[5/5] 跳过 MCP 验证（--no-verify）")

    log("")
    log("完成。生效方式：")
    log(f"  1. Skill：DSH 技能库刷新后即可用（{SKILL_NAME}）")
    log(f"  2. MCP 工具：重启 DSH（或等待 HMR 热载）后，Agent 会看到 mcp__ydmemory__recall / mcp__ydmemory__load_memory / mcp__ydmemory__memorize")
    log(f"  3. 会话结束 flush：调用 POST {args.base_url}/api/v1/learning/flush（DSH 生命周期回调或 Agent 主动调用）")


if __name__ == "__main__":
    main()
