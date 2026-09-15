"""3 MCP tools: recall / load_memory / memorize."""

from __future__ import annotations

from mcp import types

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "recall",
        "description": (
            "检索与当前任务相关的历史记忆和业务规则。\n"
            "使用场景：需要历史信息或参考知识时，在回答用户问题之前调用此工具。\n"
            "intent 参数用自然语言描述你当前需要什么信息，不需要构造关键词。\n"
            "返回结果包含记忆摘要和参考文档引用，通常不需要额外调用 load_memory。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": "用自然语言描述当前需要什么信息",
                }
            },
            "required": ["intent"],
        },
    },
    {
        "name": "load_memory",
        "description": (
            "加载指定记忆的完整内容。当 recall 返回的 summary 不够详细时使用。\n"
            "参数 id 从 recall 返回的 memories[].id 获取。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "记忆 ID，从 recall 返回的 memories[].id 获取",
                }
            },
            "required": ["id"],
        },
    },
    {
        "name": "memorize",
        "description": (
            "提交值得长期记住的信息。\n"
            "使用场景：1) 用户明确告知新规则、纠正错误 (type=user_feedback)\n"
            "2) 你判断信息有长期价值 (type=agent_mark)。\n"
            "注意：提交后不会立即生效，在对话结束后由学习模型统一分析处理。\n"
            "如需引用刚提交的信息，从对话上下文获取，不要依赖 recall。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["user_feedback", "agent_mark"],
                    "description": "user_feedback=用户显式告知/纠正; agent_mark=Agent自己判断有价值",
                },
                "context": {
                    "type": "string",
                    "description": "完整上下文描述，越具体越好",
                },
                "marked_type": {
                    "type": "string",
                    "description": "可选：user | feedback | project | reference",
                },
                "source": {
                    "type": "string",
                    "description": "可选：事件来源，默认 chat。区分 REST/IM/定时任务等入口，便于后续按来源审计与回放",
                },
                "session_id": {
                    "type": "string",
                    "description": "可选：会话 ID，用于把同一对话内多次提交的事件关联起来",
                },
                "dedup_key": {
                    "type": "string",
                    "description": "可选：去重键，相同 dedup_key 的事件在 flush 时按幂等处理，防止重复提交",
                },
            },
            "required": ["type", "context"],
        },
    },
]
