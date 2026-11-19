from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
# Project root first, backend/ second (later files win). Real environment variables
# always take precedence over both, which is how Docker Compose injects them.
ENV_FILES = (_BACKEND_DIR.parent / ".env", _BACKEND_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, env_file_encoding="utf-8", extra="ignore")

    app_name: str = "PayFlow AI - Payment Engine"
    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://payflow:payflow@localhost:5432/payflow"

    stripe_secret_key: SecretStr = Field(default=SecretStr(""))
    stripe_webhook_secret: SecretStr = Field(default=SecretStr(""))
    stripe_webhook_tolerance_seconds: int = 300
    stripe_api_timeout_seconds: int = 10

    checkout_success_url: str = "http://localhost:8000/payments/demo/success?session_id={CHECKOUT_SESSION_ID}"
    checkout_cancel_url: str = "http://localhost:8000/payments/demo/cancel"
    checkout_max_quantity: int = 10
    checkout_session_ttl_seconds: int = Field(default=2 * 3600, ge=31 * 60, le=24 * 3600)
    checkout_creation_lease_seconds: int = Field(default=120, ge=30)
    checkout_rate_limit_per_minute: int = Field(default=10, ge=0)

    demo_endpoints_enabled: bool = False

    reconcile_interval_seconds: int = Field(default=60, ge=5)
    reconcile_batch_size: int = Field(default=100, ge=1, le=1000)
    # Tolerance between our clock and Stripe's when searching sessions by creation time.
    reconcile_clock_skew_seconds: int = Field(default=300, ge=0)
    reconcile_claim_lease_seconds: int = Field(default=300, ge=30)
    reconcile_backoff_base_seconds: int = Field(default=60, ge=1)
    reconcile_backoff_max_seconds: int = Field(default=6 * 3600, ge=60)
    reconcile_open_first_check_seconds: int = Field(default=15 * 60, ge=60)

    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "qwen2.5:7b-instruct"
    ollama_embed_model: str = "bge-m3"
    # None: use the prefixes the embedding model was trained with (see app.rag.dependencies).
    ollama_embed_query_prefix: str | None = None
    ollama_embed_document_prefix: str | None = None
    ollama_timeout_seconds: float = Field(default=120.0, gt=0)
    qdrant_url: str = "http://localhost:6333"
    # Base name: the effective collection is suffixed with the embedding model, one per vector space.
    qdrant_collection: str = Field(default="payflow_rag", pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    rag_rate_limit_per_minute: int = Field(default=20, ge=0)
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_max_doc_chunks: int = Field(default=2, ge=0, le=20)
    # Calibrated for bge-m3 on the evaluation set (python -m app.rag.evaluation run); depends on the model.
    rag_min_score: float = Field(default=0.48, ge=-1.0, le=1.0)
    # SQL-first assistant, one switch per phase so each can be measured against the baseline.
    rag_document_facts: bool = True
    rag_sql_lookup: bool = True
    rag_sql_aggregates: bool = True
    rag_max_question_chars: int = Field(default=1000, ge=10, le=8000)
    rag_chunk_chars: int = Field(default=1200, ge=200, le=8000)
    rag_chunk_overlap_chars: int = Field(default=150, ge=0, le=2000)
    rag_index_interval_seconds: int = Field(default=300, ge=10)
    rag_docs_dir: str = str(_BACKEND_DIR.parent / "docs")

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    @property
    def demo_endpoints_active(self) -> bool:
        """Demo endpoints never exist in production, whatever the flag says."""
        return self.demo_endpoints_enabled and not self.is_production

    @field_validator("stripe_secret_key")
    @classmethod
    def _test_mode_secret_only(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw and not (raw.startswith("sk_test_") or raw.startswith("rk_test_")):
            raise ValueError("Only Stripe TEST mode keys (sk_test_/rk_test_) are allowed")
        return value

    @field_validator("stripe_webhook_secret")
    @classmethod
    def _webhook_secret_format(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw and not raw.startswith("whsec_"):
            raise ValueError("Stripe webhook secret must start with whsec_")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
