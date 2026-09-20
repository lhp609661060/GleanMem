"""V2 内置调度器端到端演示：不需要 LLM key、不需要外部 crontab。

验证的真实链路（不是单测的替身）：
    起服务 → 建 Space → 投事件 → PUT schedule=* * * * * → 等服务自身在下一分钟
    自动 flush → 记忆可被召回 → 调度状态可观测 → 暂停后不再触发。

用法（在 backend/ 目录，需 PG 与 alembic 已就绪）：
    uv run python scripts/e2e_scheduler_demo.py
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime

import httpx

API = "http://127.0.0.1:8011/api/v1"
ADMIN_KEY = "e2e-scheduler-admin"
ENV = {**os.environ, "YDM_ADMIN_KEY": ADMIN_KEY, "YDM_SCHEDULER_ENABLED": "true"}
HEADERS = {"Authorization": f"Bearer {ADMIN_KEY}"}


def step(n: int, msg: str) -> None:
    print(f"\n[{n}] {msg}")


async def wait_ready(client: httpx.AsyncClient, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if (await client.get("http://127.0.0.1:8011/health")).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("服务 30s 内未就绪")


async def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "gleanmem.main:app",
         "--host", "127.0.0.1", "--port", "8011"],
        env=ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            await wait_ready(c)

            step(1, "建 Space（尚无 schedule）")
            r = await c.post(f"{API}/spaces", json={"name": "e2e-scheduler"})
            assert r.status_code == 200, r.text
            agent_id = r.json()["agent_id"]
            key = r.json()["space_key"]
            print(f"    agent_id={agent_id}")

            step(2, "以 space key 投一条学习事件进收件箱")
            sh = {"Authorization": f"Bearer {key}"}
            r = await c.post(
                f"{API}/learning/events",
                headers=sh,
                json={
                    "type": "user_feedback",
                    "context": "调度器演示规则：华东客户发货统一使用中远海运",
                    "source": "chat",
                },
            )
            assert r.status_code == 200, r.text
            event_id = r.json()["event_id"]
            print(f"    event_id={event_id}（此时尚未蒸馏）")

            step(3, "PUT config.schedule='* * * * *' —— 每分钟自动 flush")
            r = await c.put(
                f"{API}/spaces/{agent_id}",
                headers=HEADERS,
                json={"config": {"schedule": "* * * * *"}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["config"]["schedule"] == "* * * * *"

            step(4, "非法 cron 必须被 API 拒绝（422）")
            r = await c.put(
                f"{API}/spaces/{agent_id}",
                headers=HEADERS,
                json={"config": {"schedule": "每天下午三点"}},
            )
            assert r.status_code == 422, f"期望 422，实得 {r.status_code}"
            print("    已拒绝，且原 schedule 未被破坏")

            step(5, "等待服务自身触发（最多 80s，无外部 crontab）")
            start = datetime.now()
            memories: list = []
            while (datetime.now() - start).total_seconds() < 80:
                r = await c.get(f"{API}/memories", headers=sh)
                memories = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
                if memories:
                    break
                await asyncio.sleep(3)
            assert memories, "80s 内调度器未产出记忆 —— 内置调度未生效"
            print(f"    等位 {int((datetime.now()-start).total_seconds())}s 后自动出记忆：")
            for m in memories:
                print(f"      - [{m['type']}] {m['title']}")

            step(6, "GET /learning/schedules —— 调度可观测")
            r = await c.get(f"{API}/learning/schedules", headers=sh)
            sched = r.json()
            assert sched["scheduler_enabled"] is True
            item = sched["items"][0]
            print(f"    cron={item['cron']} valid={item['valid']} "
                  f"next={item['next_fire_at']} last_flush={item['last_scheduled_flush']}")
            assert item["last_scheduled_flush"], "触发未留痕"

            step(7, "召回侧能查到调度器蒸馏出的记忆")
            r = await c.post(
                f"{API}/recall", json={"intent": "华东客户发什么快递"}, headers=sh
            )
            assert r.status_code == 200, r.text
            hits = r.json()["memories"]
            assert hits, "召回为空"
            top = hits[0]
            print(f"    Top1={top.get('title')} (score={top.get('score')})")

            step(8, "暂停调度：schedule_enabled=false 后槽位不再前进")
            await c.put(
                f"{API}/spaces/{agent_id}",
                headers=HEADERS,
                json={"config": {"schedule_enabled": False}},
            )
            r = await c.get(f"{API}/learning/schedules", headers=sh)
            assert r.json()["items"][0]["enabled"] is False
            slot_before = r.json()["items"][0]["last_fired_slot"]
            await asyncio.sleep(65)
            r = await c.get(f"{API}/learning/schedules", headers=sh)
            assert r.json()["items"][0]["last_fired_slot"] == slot_before, "暂停后仍在触发"
            print(f"    暂停 65s，last_fired_slot 保持 {slot_before}")

            step(9, "清理演示 Space")
            r = await c.delete(f"{API}/spaces/{agent_id}", headers=HEADERS)
            assert r.status_code == 200, r.text

        print("\n" + "=" * 60)
        print("✅ V2 内置调度器端到端 9 步全部通过")
        print("=" * 60)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ 演示失败：{type(exc).__name__}: {exc}")
        print("---- 服务日志（尾 40 行）----")
        if proc.stdout:
            import itertools

            for line in itertools.islice(proc.stdout, 0, None):
                print(line.rstrip())
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
