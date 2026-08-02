from __future__ import annotations

import json

import httpx
import redis

from backend.app.config import get_settings

ALERT_QUEUE_KEY = "avatar:alerts"


def deliver_feishu_alert(webhook: str, title: str, detail: str) -> bool:
    if not webhook:
        return False
    text = f"【{title}】\n{detail}"
    try:
        response = httpx.post(
            webhook,
            json={"msg_type": "text", "content": {"text": text}},
            timeout=3.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    return True


def send_feishu_alert(title: str, detail: str) -> bool:
    settings = get_settings()
    if not settings.feishu_alert_webhook:
        return False
    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.25,
            socket_timeout=0.5,
        )
        client.rpush(
            ALERT_QUEUE_KEY,
            json.dumps(
                {"title": title, "detail": detail},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
    except redis.RedisError:
        return False
    return True
