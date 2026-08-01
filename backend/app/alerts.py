from __future__ import annotations

import httpx

from backend.app.config import get_settings


def send_feishu_alert(title: str, detail: str) -> bool:
    webhook = get_settings().feishu_alert_webhook
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
