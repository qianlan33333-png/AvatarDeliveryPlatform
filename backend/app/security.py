from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from backend.app.config import Settings, get_settings

_PHONE_PATTERN = re.compile(r"^1\d{10}$")


class SecurityValueError(ValueError):
    pass


def normalize_phone(raw_phone: str) -> str:
    normalized = re.sub(r"[\s()-]", "", raw_phone.strip())
    if normalized.startswith("+86"):
        normalized = normalized[3:]
    elif normalized.startswith("0086"):
        normalized = normalized[4:]
    if not _PHONE_PATTERN.fullmatch(normalized):
        raise SecurityValueError("手机号格式不正确")
    return normalized


def lookup_phone_hash(phone: str, settings: Settings | None = None) -> str:
    configured = settings or get_settings()
    return hmac.new(
        configured.phone_lookup_pepper.encode(),
        phone.encode(),
        hashlib.sha256,
    ).hexdigest()


def _fernet(purpose: str, key_material: str, settings: Settings) -> Fernet:
    source = key_material or settings.app_secret_key
    digest = hashlib.sha256(f"{purpose}:{source}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_value(
    value: str,
    *,
    purpose: str,
    key_material: str = "",
    settings: Settings | None = None,
) -> str:
    configured = settings or get_settings()
    return _fernet(purpose, key_material, configured).encrypt(value.encode()).decode()


def decrypt_value(
    value: str,
    *,
    purpose: str,
    key_material: str = "",
    settings: Settings | None = None,
) -> str:
    configured = settings or get_settings()
    try:
        return _fernet(purpose, key_material, configured).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecurityValueError("加密数据无法解密") from exc


def encrypt_phone(phone: str, settings: Settings | None = None) -> str:
    configured = settings or get_settings()
    return encrypt_value(
        phone,
        purpose="phone",
        key_material=configured.phone_encryption_key,
        settings=configured,
    )


def decrypt_phone(ciphertext: str, settings: Settings | None = None) -> str:
    configured = settings or get_settings()
    return decrypt_value(
        ciphertext,
        purpose="phone",
        key_material=configured.phone_encryption_key,
        settings=configured,
    )


def _serializer(salt: str, settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.app_secret_key, salt=salt)


def issue_scoped_token(
    payload: dict[str, Any],
    *,
    salt: str,
    settings: Settings | None = None,
) -> str:
    configured = settings or get_settings()
    return _serializer(salt, configured).dumps(payload)


def load_scoped_token(
    token: str,
    *,
    salt: str,
    max_age_seconds: int,
    settings: Settings | None = None,
) -> dict[str, Any]:
    configured = settings or get_settings()
    try:
        value = _serializer(salt, configured).loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired) as exc:
        raise SecurityValueError("令牌无效或已过期") from exc
    if not isinstance(value, dict):
        raise SecurityValueError("令牌内容无效")
    return value


def issue_user_token(user_id: str, settings: Settings | None = None) -> str:
    return issue_scoped_token({"sub": user_id}, salt="user-access-v1", settings=settings)


def load_user_token(token: str, settings: Settings | None = None) -> str:
    configured = settings or get_settings()
    payload = load_scoped_token(
        token,
        salt="user-access-v1",
        max_age_seconds=configured.user_token_max_age_seconds,
        settings=configured,
    )
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise SecurityValueError("令牌缺少用户标识")
    return user_id
