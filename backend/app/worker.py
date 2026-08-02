from __future__ import annotations

import json
import signal
import time
from typing import Any

import redis

from backend.app.alerts import ALERT_QUEUE_KEY, deliver_feishu_alert
from backend.app.config import get_settings

HEARTBEAT_KEY = "avatar:worker:heartbeat"
DEAD_LETTER_KEY = "avatar:alerts:dead"
_running = True


def _stop(_: int, __: Any) -> None:
    global _running
    _running = False


def _process_alert(raw_payload: bytes, client: redis.Redis) -> None:
    settings = get_settings()
    try:
        payload = json.loads(raw_payload)
        title = str(payload["title"])
        detail = str(payload["detail"])
    except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
        client.rpush(DEAD_LETTER_KEY, raw_payload)
        return
    if not deliver_feishu_alert(settings.feishu_alert_webhook, title, detail):
        client.rpush(DEAD_LETTER_KEY, raw_payload)


def run_worker() -> None:
    settings = get_settings()
    client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while _running:
        now = str(time.time())
        client.set(HEARTBEAT_KEY, now, ex=35)
        item = client.blpop(ALERT_QUEUE_KEY, timeout=5)
        if item:
            _process_alert(item[1], client)
    client.set(HEARTBEAT_KEY, str(time.time()), ex=5)


if __name__ == "__main__":
    run_worker()
