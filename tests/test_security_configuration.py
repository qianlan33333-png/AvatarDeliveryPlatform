from __future__ import annotations

import pytest

from backend.app.config import Settings
from backend.app.modules.ai_models.service import validate_model_configuration


def _production_settings(**overrides: str) -> Settings:
    values = {
        "app_env": "production",
        "app_secret_key": "a" * 32,
        "phone_encryption_key": "b" * 32,
        "phone_lookup_pepper": "c" * 32,
        "llm_encryption_key": "d" * 32,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("app_secret_key", "development-only-secret-change-me"),
        ("phone_encryption_key", "short"),
        ("phone_lookup_pepper", "development-phone-pepper"),
        ("llm_encryption_key", ""),
    ],
)
def test_production_rejects_default_or_short_key_material(field: str, value: str) -> None:
    settings = _production_settings(**{field: value})

    with pytest.raises(RuntimeError, match=field.upper()):
        settings.validate_runtime_security()


def test_production_accepts_independent_strong_key_material() -> None:
    _production_settings().validate_runtime_security()


def _validate_url(base_url: str, settings: Settings) -> None:
    validate_model_configuration(
        provider="custom",
        capability="chat",
        base_url=base_url,
        model_name="test-model",
        embedding_dimension=None,
        settings=settings,
    )


def test_model_base_url_requires_https_in_production() -> None:
    settings = _production_settings()

    _validate_url("https://models.example.com/v1", settings)
    with pytest.raises(ValueError, match="HTTPS"):
        _validate_url("http://127.0.0.1:11434/v1", settings)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
        "http://[::1]:11434/v1",
    ],
)
def test_development_allows_only_loopback_http(base_url: str) -> None:
    settings = Settings(_env_file=None, app_env="development")
    _validate_url(base_url, settings)


def test_development_rejects_remote_http_and_url_credentials() -> None:
    settings = Settings(_env_file=None, app_env="development")

    with pytest.raises(ValueError, match="loopback"):
        _validate_url("http://models.example.com/v1", settings)
    with pytest.raises(ValueError, match="invalid base URL"):
        _validate_url("https://user:password@models.example.com/v1", settings)
