"""Single source of non-LLM configuration — pydantic-settings, injected
everywhere. Nothing reads ``os.environ`` ad hoc. All model/provider settings
live separately in ``config/llm.yml`` (see ``app.config.llm_config``).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    #  core 
    app_env: str = "production"
    secret_key: str
    frontend_origin: str = "http://localhost:5173"  # comma-separated, split below

    #  infrastructure
    database_url: str
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672//"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "documents"

    #  auth / jwt
    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 7
    cookie_secure: bool = True
    cookie_domain: str | None = None

    #  oauth 
    google_client_id: str | None = None
    google_client_secret: str | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None
    microsoft_tenant: str = "common"
    github_client_id: str | None = None
    github_client_secret: str | None = None
    oauth_redirect_base: str = "http://localhost:8000/api/auth"

    #  email policy 
    email_allowlist: str = ""  # comma-separated domains; empty => allow all
    disposable_block: bool = True
    require_email_verification: bool = True

    #  smtp (verification / reset mail) 
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "no-reply@openrag.local"

    #  llm + embeddings
    # All model/provider config lives in config/llm.yml (see app.config.llm_config).

    #  limits
    max_upload_mb: int = 50                 # per-file cap
    max_files_per_session: int = 50         # documents per chat
    max_session_total_mb: int = 1024        # total documents per chat (1 GB)
    max_tokens_per_user_day: int = 200_000
    max_tool_iterations: int = 4
    upload_dir: str = "/data/uploads"

    rate_limit_auth: str = "5/minute"
    rate_limit_chat: str = "20/minute"
    rate_limit_upload: str = "30/hour"

    #  derived 
    @computed_field
    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]

    @property
    def allowlist_domains(self) -> set[str]:
        return {d.strip().lower() for d in self.email_allowlist.split(",") if d.strip()}

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_session_total_bytes(self) -> int:
        return self.max_session_total_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
