from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YDM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Postgres
    database_url: str = "postgresql+asyncpg://ydm:ydm@localhost:5432/ydm"

    # LLM (optional — LearningModel in llm mode)
    llm_api_key: str = ""
    llm_api_base: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # 浏览器跨域来源白名单（逗号分隔）。默认 * 便于本地前端开发；
    # 生产用 YDM_CORS_ORIGINS=https://a.com,https://b.com 收紧。
    cors_origins: str = "*"

    # 平台管理台 admin key（YDM_ADMIN_KEY）；为空则禁用平台管理（管理台/用户管理）
    admin_key: str = ""

    # MCP SSE 身份来源：默认要求 X-Space-Key（与 REST 同一套强度）。
    # 置 false 回退旧的「仅信 X-Agent-ID」约定，只供内网演示/spike，公网禁用。
    mcp_auth_required: bool = True

    # 内置调度器（V2）：Space 级 cron 自动 flush
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 20  # 判定粒度是分钟，20s 足够且不空转
    scheduler_catchup_window_minutes: int = 720  # 补跑只回看这么久

    # 单次 flush 取走的事件上限（D18）：钉死一次 LLM 请求的 payload 规模，
    # 超出的留在收件箱等下一次 flush（webhook 或 cron）。
    flush_batch_size: int = 50

    # 事件重试达阈值后转死信（status='dead'）留在库里，不再删除（D19）
    event_max_retries: int = 3

    # Defaults for new Agent Spaces
    default_max_memories: int = 5000
    default_decay_per_day: float = 0.95
    default_min_weight: float = 0.1
    default_learning_mode: str = "heuristic"  # llm | heuristic | direct

    # zhparser config (pg_conf)
    zhparser_dict: str = "zhparser"

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()

