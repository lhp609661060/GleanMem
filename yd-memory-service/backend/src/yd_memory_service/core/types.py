from __future__ import annotations

from enum import StrEnum


class MemoryType(StrEnum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"
    TASK = "task"


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
