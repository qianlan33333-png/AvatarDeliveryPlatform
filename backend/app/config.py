from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "分身交付平台"
    app_env: str = "development"
    app_version: str = "local"
    app_secret_key: str = Field(default="development-only-secret-change-me")
    database_url: str = "sqlite+pysqlite:///./avatar_delivery.db"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_required: bool = False

    admin_username: str = "admin"
    admin_bootstrap_password: str = ""
    phone_encryption_key: str = ""
    phone_lookup_pepper: str = "development-phone-pepper"

    wechat_app_id: str = "wx318698f4c753111e"
    wechat_app_secret: str = ""
    wechat_api_base_url: str = "https://api.weixin.qq.com"
    user_token_max_age_seconds: int = 30 * 24 * 60 * 60

    entitlement_webhook_secret: str = ""
    webhook_max_clock_skew_seconds: int = 300

    tencent_vod_secret_id: str = ""
    tencent_vod_secret_key: str = ""
    tencent_vod_sub_app_id: int = 0
    tencent_vod_procedure: str = "avatarDeliveryHLS"
    tencent_vod_storage_region: str = "ap-beijing"
    tencent_vod_callback_token: str = ""

    llm_encryption_key: str = ""
    public_base_url: str = "http://127.0.0.1:8080"
    playback_lease_ttl_seconds: int = 120
    playback_heartbeat_interval_seconds: int = 30
    playback_session_limit: int = 100
    playback_alert_threshold: int = 80
    playback_priority_alert_threshold: int = 90
    playback_redirect_token_max_age_seconds: int = 180
    chat_reservation_ttl_seconds: int = 60
    llm_concurrency_limit: int = 10
    llm_request_timeout_seconds: int = 30
    knowledge_internal_token: str = ""
    knowledge_injection_enabled: bool = False
    knowledge_context_max_chars: int = 6000
    knowledge_embedding_batch_size: int = 32
    knowledge_index_max_attempts: int = 5
    knowledge_index_retry_base_seconds: int = 15
    knowledge_index_running_timeout_seconds: int = 300
    feishu_alert_webhook: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def validate_runtime_security(self) -> None:
        """Fail closed when production would reuse public or weak key material."""

        if not self.is_production:
            return
        required_secrets = {
            "APP_SECRET_KEY": self.app_secret_key,
            "PHONE_ENCRYPTION_KEY": self.phone_encryption_key,
            "PHONE_LOOKUP_PEPPER": self.phone_lookup_pepper,
            "LLM_ENCRYPTION_KEY": self.llm_encryption_key,
        }
        known_defaults = {
            "development-only-secret-change-me",
            "development-phone-pepper",
        }
        invalid = sorted(
            name
            for name, value in required_secrets.items()
            if len(value.strip()) < 32
            or value.strip() in known_defaults
            or value.strip().lower().startswith("replace-")
        )
        if invalid:
            raise RuntimeError(
                "insecure production secret configuration: " + ", ".join(invalid)
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime_security()
    return settings
