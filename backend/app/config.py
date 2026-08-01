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

    tencent_vod_secret_id: str = ""
    tencent_vod_secret_key: str = ""
    tencent_vod_sub_app_id: int = 0
    tencent_vod_procedure: str = "avatarDeliveryAdaptiveHLS"
    tencent_vod_storage_region: str = "ap-beijing"
    tencent_vod_callback_token: str = ""

    llm_encryption_key: str = ""
    feishu_alert_webhook: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

