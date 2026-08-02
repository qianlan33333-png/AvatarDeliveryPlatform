from typing import Any

import redis
from fastapi import APIRouter
from sqlalchemy import text

from backend.app.config import get_settings
from backend.app.db import get_engine

router = APIRouter(tags=["health"])


def _database_status() -> tuple[bool, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:  # pragma: no cover - exercised against real infrastructure
        return False, exc.__class__.__name__


def _redis_status() -> tuple[bool, str]:
    settings = get_settings()
    try:
        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.25)
        client.ping()
        return True, "ok"
    except Exception as exc:
        if not settings.redis_required:
            return True, f"optional:{exc.__class__.__name__}"
        return False, exc.__class__.__name__


@router.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    database_ok, database_detail = _database_status()
    redis_ok, redis_detail = _redis_status()
    healthy = database_ok and redis_ok
    return {
        "ok": healthy,
        "status": "ok" if healthy else "degraded",
        "service": "avatar-delivery-platform",
        "version": settings.app_version,
        "environment": settings.app_env,
        "database": database_detail,
        "redis": redis_detail,
        "integrations": {
            "wechat": bool(settings.wechat_app_id and settings.wechat_app_secret),
            "vod": bool(settings.tencent_vod_secret_id and settings.tencent_vod_secret_key),
            "entitlement_webhook": bool(settings.entitlement_webhook_secret),
            "feishu_alert": bool(settings.feishu_alert_webhook),
        },
    }
