from __future__ import annotations

import json
from types import SimpleNamespace

from backend.app import alerts, worker


class FakeRedis:
    def __init__(self) -> None:
        self.items: list[tuple[str, bytes | str]] = []

    def rpush(self, key: str, value: bytes | str) -> None:
        self.items.append((key, value))


def test_alert_is_queued_without_blocking_on_feishu(monkeypatch) -> None:
    fake_redis = FakeRedis()
    monkeypatch.setattr(
        alerts,
        "get_settings",
        lambda: SimpleNamespace(
            feishu_alert_webhook="https://example.invalid/webhook",
            redis_url="redis://example/0",
        ),
    )
    monkeypatch.setattr(alerts.redis.Redis, "from_url", lambda *args, **kwargs: fake_redis)

    assert alerts.send_feishu_alert("并发预警", "当前 80 人") is True
    key, raw_payload = fake_redis.items[0]
    assert key == alerts.ALERT_QUEUE_KEY
    assert json.loads(str(raw_payload)) == {"title": "并发预警", "detail": "当前 80 人"}


def test_worker_delivers_valid_alert_and_dead_letters_invalid_payload(monkeypatch) -> None:
    fake_redis = FakeRedis()
    delivered: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(feishu_alert_webhook="https://example.invalid/webhook"),
    )
    monkeypatch.setattr(
        worker,
        "deliver_feishu_alert",
        lambda webhook, title, detail: delivered.append((webhook, title, detail)) or True,
    )

    worker._process_alert('{"title":"容量","detail":"已满"}'.encode(), fake_redis)
    worker._process_alert(b"not-json", fake_redis)

    assert delivered == [("https://example.invalid/webhook", "容量", "已满")]
    assert fake_redis.items == [(worker.DEAD_LETTER_KEY, b"not-json")]
