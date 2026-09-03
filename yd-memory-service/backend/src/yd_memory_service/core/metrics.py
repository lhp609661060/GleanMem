"""轻量运行时指标：进程内计数器 + 耗时累计，无外部依赖。

设计取舍：不引入 prometheus_client（重依赖 + 多一个生态），用进程内计数器覆盖
核心可观测需求——flush/recall 调用次数与耗时、LLM token 消耗。适合单实例部署；
多实例需换 Prometheus exporter 或共享存储（届时再升级，不为假设提前引入）。
"""
from __future__ import annotations

import time
from threading import Lock


class _LatencyCounter:
    """调用计数 + 耗时累计，派生平均耗时。"""

    def __init__(self) -> None:
        self.count = 0
        self.total_ms = 0.0

    def observe(self, ms: float) -> None:
        self.count += 1
        self.total_ms += ms

    def snapshot(self) -> dict:
        avg = self.total_ms / self.count if self.count else 0.0
        return {
            "count": self.count,
            "total_ms": round(self.total_ms, 2),
            "avg_ms": round(avg, 2),
        }


class _TokenUsage:
    """LLM token 消耗累计。"""

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt += prompt
        self.completion += completion

    def snapshot(self) -> dict:
        return {
            "prompt_tokens": self.prompt,
            "completion_tokens": self.completion,
            "total_tokens": self.prompt + self.completion,
        }


class Metrics:
    """线程安全的运行时指标注册表。用 `observe_*` 上报，`snapshot` 读出。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self.flush = _LatencyCounter()
        self.recall = _LatencyCounter()
        self.llm_tokens = _TokenUsage()

    def observe_flush(self, ms: float) -> None:
        with self._lock:
            self.flush.observe(ms)

    def observe_recall(self, ms: float) -> None:
        with self._lock:
            self.recall.observe(ms)

    def observe_llm_tokens(self, prompt: int, completion: int) -> None:
        with self._lock:
            self.llm_tokens.add(prompt, completion)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "flush": self.flush.snapshot(),
                "recall": self.recall.snapshot(),
                "llm_tokens": self.llm_tokens.snapshot(),
            }


metrics = Metrics()
"""全局单例。各业务路径直接 `from yd_memory_service.core.metrics import metrics` 上报。"""


def timed_flush():
    """上下文管理器：计量一次 flush 耗时并上报。"""

    class _Ctx:
        def __enter__(self):
            self._t0 = time.perf_counter()
            return self

        def __exit__(self, *_exc):
            metrics.observe_flush((time.perf_counter() - self._t0) * 1000)
            return False

    return _Ctx()


def timed_recall():
    """上下文管理器：计量一次 recall 耗时并上报。"""

    class _Ctx:
        def __enter__(self):
            self._t0 = time.perf_counter()
            return self

        def __exit__(self, *_exc):
            metrics.observe_recall((time.perf_counter() - self._t0) * 1000)
            return False

    return _Ctx()
