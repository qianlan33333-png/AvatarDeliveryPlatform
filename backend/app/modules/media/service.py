from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode


class VODConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadSignature:
    value: str
    expires_at: int
    original: str


def build_upload_signature(
    *,
    secret_id: str,
    secret_key: str,
    session_context: str,
    sub_app_id: int = 0,
    procedure: str = "",
    storage_region: str = "",
    current_timestamp: int | None = None,
    random_value: int | None = None,
    lifetime_seconds: int = 600,
) -> UploadSignature:
    if not secret_id or not secret_key:
        raise VODConfigurationError("腾讯云 VOD 密钥尚未配置")
    now = current_timestamp if current_timestamp is not None else int(time.time())
    nonce = random_value if random_value is not None else secrets.randbelow(2_147_483_647) + 1
    expires_at = now + min(max(lifetime_seconds, 60), 86_400)
    params: dict[str, str | int] = {
        "secretId": secret_id,
        "currentTimeStamp": now,
        "expireTime": expires_at,
        "random": nonce,
        "oneTimeValid": 1,
        "sessionContext": session_context,
    }
    if sub_app_id:
        params["vodSubAppId"] = sub_app_id
    if procedure:
        params["procedure"] = procedure
    if storage_region:
        params["storageRegion"] = storage_region
    original = urlencode(params)
    digest = hmac.new(secret_key.encode(), original.encode(), hashlib.sha1).digest()
    encoded = base64.b64encode(digest + original.encode()).decode()
    return UploadSignature(value=encoded, expires_at=expires_at, original=original)


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(normalized.encode()).hexdigest()


def external_event_id(payload: Mapping[str, Any], payload_hash: str) -> str:
    for key in ("EventId", "EventID", "EventHandle"):
        value = payload.get(key)
        if value:
            return str(value)[:180]
    event_type = str(payload.get("EventType", "unknown"))
    event_data = event_data_for(payload)
    for key in ("TaskId", "ProcedureTaskId", "FileId"):
        value = event_data.get(key)
        if value:
            return f"{event_type}:{value}"[:180]
    return f"{event_type}:{payload_hash}"[:180]


def event_data_for(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    event_type = str(payload.get("EventType", ""))
    candidates = {
        "NewFileUpload": "FileUploadEvent",
        "ProcedureStateChanged": "ProcedureStateChangeEvent",
        "FileDeleted": "FileDeleteEvent",
    }
    selected = payload.get(candidates.get(event_type, ""), {})
    return selected if isinstance(selected, Mapping) else {}


def session_context_asset_id(payload: Mapping[str, Any]) -> str:
    event_data = event_data_for(payload)
    raw = event_data.get("SessionContext") or event_data.get("SourceContext") or ""
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return raw if len(raw) == 36 else ""
    return str(decoded.get("asset_id", "")) if isinstance(decoded, Mapping) else ""


def first_recursive_value(value: Any, keys: set[str]) -> Any:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in keys and child not in (None, ""):
                return child
        for child in value.values():
            found = first_recursive_value(child, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            found = first_recursive_value(child, keys)
            if found not in (None, ""):
                return found
    return None


def collect_recursive_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() == "url" and isinstance(child, str):
                urls.append(child)
            else:
                urls.extend(collect_recursive_urls(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            urls.extend(collect_recursive_urls(child))
    return urls


def playback_url_from(payload: Mapping[str, Any]) -> str:
    urls = collect_recursive_urls(event_data_for(payload))
    for url in urls:
        if ".m3u8" in url.lower():
            return url
    return urls[0] if urls else ""


def duration_from(payload: Mapping[str, Any]) -> int:
    raw = first_recursive_value(
        event_data_for(payload), {"VideoDuration", "Duration", "duration"}
    )
    try:
        return max(0, int(round(float(raw))))
    except (TypeError, ValueError):
        return 0
