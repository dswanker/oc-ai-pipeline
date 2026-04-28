"""
Application settings, loaded from environment variables.

Backed by pydantic-settings, which reads from the process environment
and `.env` (if present). Centralizing config here means modules don't
read os.environ directly — they import `settings` and use it.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Service ──────────────────────────────────────────────────
    trainer_port: int = 8001
    trainer_log_level: str = "info"

    # ─── Anthropic ────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    anthropic_model: str = "claude-opus-4-7"

    # ─── monday.com ───────────────────────────────────────────────
    monday_api_token: str = ""
    monday_corpus_board_id: int = 18410424473
    monday_webhook_secret: str = ""

    # ─── clinicaltrials.gov ───────────────────────────────────────
    ctgov_user_agent: str = "oc-study-build-trainer/0.1"
    ctgov_auto_ingest_threshold: float = 0.9

    # ─── Embeddings ───────────────────────────────────────────────
    embed_model_name: str = "BAAI/bge-large-en-v1.5"
    embed_device: str = "cpu"  # or "cuda"

    # ─── Vector store ─────────────────────────────────────────────
    vector_db_path: str = "./corpus/embeddings.db"
    corpus_cache_dir: str = "./corpus/cache"


settings = Settings()
