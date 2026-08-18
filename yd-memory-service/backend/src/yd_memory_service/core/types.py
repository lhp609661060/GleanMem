from __future__ import annotations

from enum import StrEnum


class MemoryType(StrEnum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"
    TASK = "task"
    PATTERN = "pattern"  # V1.5 归纳产物：受审核纪律约束（见 MODERATED_TYPES）


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    FLAGGED = "flagged"
    DEPRECATED = "deprecated"


# 防幻觉纪律（01-design §b）：归纳类产物必须人工审核通过才能进召回。
# 其余类型 pending 即可召回（V1 简化决策：聊天/观察素材是人喂的，不是 LLM 归纳的）。
MODERATED_TYPES = frozenset({MemoryType.PATTERN.value})

# 任何状态下都不该进召回的状态（无论类型）
EXCLUDED_STATUSES = frozenset({ReviewStatus.FLAGGED.value, ReviewStatus.DEPRECATED.value})


class EventType(StrEnum):
    USER_FEEDBACK = "user_feedback"
    AGENT_MARK = "agent_mark"


class LearningMode(StrEnum):
    LLM = "llm"
    HEURISTIC = "heuristic"
    DIRECT = "direct"


class DecisionAction(StrEnum):
    STORE = "store"
    DISCARD = "discard"
    UPDATE = "update"
    MERGE = "merge"
    FAILED = "failed"
