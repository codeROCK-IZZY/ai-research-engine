"""Application configuration from ``RKE_*`` environment variables (or a git-ignored ``.env``).

Nothing secret has a default: the app refuses to start without ``RKE_JWT_SECRET``.
The Anthropic key is read by the SDK from ``ANTHROPIC_API_KEY``; when it is absent the
platform runs in offline *extractive* mode.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RKE_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://rke:rke@localhost:5432/rke"

    # --- security ---
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    login_rate_limit_per_minute: int = 10
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # --- storage & upload limits ---
    file_dir: Path = Path("./var/files")
    s3_bucket: str | None = None
    max_upload_mb: int = 25
    max_pages: int = 300
    max_docx_uncompressed_mb: int = 200

    # --- AI providers ---
    llm_provider: Literal["auto", "claude", "extractive"] = "auto"
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    claude_fallbacks: bool = True
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_candidates: int = 15  # hybrid recall@10 is 100% on the benchmark; 15 halves re-rank cost vs 30
    rerank_by_default: bool = True
    embedding_cache_dir: str | None = None  # baked into the Docker image at build time
    # ONNX Runtime threads per process. Default (all cores) oversubscribes the CPU when the API
    # and worker run side by side — measured ~10x slower in docker compose. None = library default.
    embedding_threads: int | None = None
    ocr_languages: str = "eng+fra+spa"

    # --- connectors ---
    url_allowlist: list[str] = Field(default_factory=list)  # empty = any public HTTPS host
    url_timeout_seconds: float = 15
    url_max_mb: int = 10
    zip_max_entries: int = 500
    zip_max_uncompressed_mb: int = 200

    # --- retrieval ---
    retrieval_top_k: int = 8
    min_relevance: float = 0.35  # vector-similarity gate, used only when re-ranking is off
    # Re-ranker gate: answer "not found" (without calling the LLM) when the best passage's
    # cross-encoder relevance is below this. Chosen from the evaluation corpus, where answerable
    # questions scored >= 0.37 and off-topic ones ~0.0; see docs/DESIGN.md section 10.
    min_rerank_relevance: float = 0.2
    rerank_answer_sentences: bool = True

    # --- logging ---
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("jwt_secret")
    @classmethod
    def _secret_strength(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("RKE_JWT_SECRET must be at least 32 characters")
        return v

    @property
    def llm_mode(self) -> Literal["claude", "extractive"]:
        """Resolve ``auto``: Claude when an API key is configured, otherwise extractive."""
        if self.llm_provider == "auto":
            return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "extractive"
        return self.llm_provider


@lru_cache
def get_settings() -> Settings:
    return Settings()
