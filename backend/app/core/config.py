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
    checkout_rate_limit_per_minute: int = Field(default=10, ge=0)

    demo_endpoints_enabled: bool = False

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
