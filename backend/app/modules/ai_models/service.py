from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.models import AIModelBinding, LLMConfig
from backend.app.security import decrypt_value

ModelCapability = Literal["chat", "embedding"]
ModelScene = Literal["qa", "copywriting", "internal_classifier", "embedding"]

MODEL_CAPABILITIES: tuple[ModelCapability, ...] = ("chat", "embedding")
MODEL_SCENES: tuple[ModelScene, ...] = (
    "qa",
    "copywriting",
    "internal_classifier",
    "embedding",
)

SCENE_LABELS: dict[str, str] = {
    "qa": "问答主模型",
    "copywriting": "话术主模型",
    "internal_classifier": "内部分类模型",
    "embedding": "Embedding 模型",
}

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "capabilities": ("chat",),
        "default_model": "deepseek-v4-flash",
    },
    "kimi": {
        "label": "Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "capabilities": ("chat",),
    },
    "zhipu": {
        "label": "智谱",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "capabilities": ("chat", "embedding"),
    },
    "doubao": {
        "label": "豆包",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "capabilities": ("chat", "embedding"),
    },
    "qwen": {
        "label": "千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "capabilities": ("chat", "embedding"),
    },
    "hunyuan": {
        "label": "腾讯混元",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "capabilities": ("chat",),
    },
    "tokenhub": {
        "label": "腾讯 TokenHub",
        "base_url": "https://tokenhub.tencentmaas.com/v1",
        "capabilities": ("embedding",),
        "default_model": "kinfra-text-embedding-0.6b",
        "embedding_dimension": 1024,
    },
    "custom": {
        "label": "自定义 OpenAI-compatible",
        "base_url": "",
        "capabilities": ("chat", "embedding"),
    },
}


@dataclass(frozen=True)
class ModelChain:
    primary: LLMConfig | None
    fallback: LLMConfig | None


def embedding_model_version_from_fields(
    *,
    provider: str,
    base_url: str,
    model_name: str,
    embedding_dimension: int | None,
) -> str:
    """Stable vector identity; credential rotation intentionally does not change it."""

    payload = {
        "schema": "embedding-generation/v1",
        "provider": provider.strip().casefold(),
        "base_url": base_url.strip().rstrip("/"),
        "model_name": model_name.strip(),
        "dimension": embedding_dimension or 1024,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"embedding-v1:{hashlib.sha256(encoded.encode()).hexdigest()}"


def embedding_model_version(config: LLMConfig) -> str:
    if config.capability != "embedding":
        raise ValueError("selected model is not an embedding model")
    return embedding_model_version_from_fields(
        provider=config.provider,
        base_url=config.base_url,
        model_name=config.model_name,
        embedding_dimension=config.embedding_dimension,
    )


def provider_preset(provider: str) -> dict[str, Any]:
    return PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])


def validate_model_configuration(
    *,
    provider: str,
    capability: str,
    base_url: str,
    model_name: str,
    embedding_dimension: int | None,
    settings: Settings | None = None,
) -> None:
    configured = settings or get_settings()
    provider = provider or "custom"
    capability = capability or "chat"
    if provider not in PROVIDER_PRESETS:
        raise ValueError("unsupported provider")
    if capability not in MODEL_CAPABILITIES:
        raise ValueError("unsupported capability")
    if capability not in provider_preset(provider)["capabilities"]:
        raise ValueError("provider does not support this capability preset")
    parsed = urlparse(base_url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("invalid base URL")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("invalid base URL") from exc
    if parsed.scheme == "http":
        if configured.is_production:
            raise ValueError("production model base URL must use HTTPS")
        hostname = parsed.hostname.casefold()
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = hostname == "localhost"
        if not is_loopback:
            raise ValueError("development HTTP model base URL must be loopback")
    if not model_name.strip():
        raise ValueError("model name is required")
    if capability == "embedding" and embedding_dimension != 1024:
        raise ValueError("V1 embedding dimension must be 1024")


def _config_is_eligible(config: LLMConfig | None, capability: ModelCapability) -> bool:
    return bool(config and config.is_active and config.capability == capability)


def resolve_model_chain(
    db: Session,
    *,
    scene: ModelScene,
    capability: ModelCapability,
) -> ModelChain:
    binding = db.scalar(select(AIModelBinding).where(AIModelBinding.scene == scene))
    if binding:
        primary = db.get(LLMConfig, binding.primary_model_id)
        fallback = (
            db.get(LLMConfig, binding.fallback_model_id)
            if binding.fallback_model_id
            else None
        )
        return ModelChain(
            primary=primary if _config_is_eligible(primary, capability) else None,
            fallback=fallback if _config_is_eligible(fallback, capability) else None,
        )

    # Backward compatibility for installations that only have the original active chat config.
    primary = db.scalar(
        select(LLMConfig)
        .where(
            LLMConfig.is_active.is_(True),
            LLMConfig.capability == capability,
        )
        .order_by(LLMConfig.updated_at.desc())
    )
    return ModelChain(primary=primary, fallback=None)


def build_openai_client(
    config: LLMConfig,
    settings: Settings | None = None,
) -> OpenAI:
    configured = settings or get_settings()
    validate_model_configuration(
        provider=config.provider,
        capability=config.capability,
        base_url=config.base_url,
        model_name=config.model_name,
        embedding_dimension=config.embedding_dimension,
        settings=configured,
    )
    api_key = decrypt_value(
        config.api_key_ciphertext,
        purpose="llm-api-key",
        key_material=configured.llm_encryption_key,
        settings=configured,
    )
    return OpenAI(
        api_key=api_key,
        base_url=config.base_url,
        timeout=configured.llm_request_timeout_seconds,
    )


def completion_overrides(config: LLMConfig) -> dict[str, Any]:
    """Provider options used only before any visible token has been emitted."""
    hostname = (urlparse(config.base_url).hostname or "").lower()
    if hostname == "api.deepseek.com" and config.model_name.startswith("deepseek-v4-"):
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {}


def embed_texts(
    config: LLMConfig,
    texts: list[str],
    settings: Settings | None = None,
) -> list[list[float]]:
    if config.capability != "embedding":
        raise ValueError("selected model is not an embedding model")
    if not texts:
        return []
    client = build_openai_client(config, settings)
    response = client.embeddings.create(model=config.model_name, input=texts)
    vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    expected_dimension = config.embedding_dimension or 1024
    if any(len(vector) != expected_dimension for vector in vectors):
        raise ValueError("embedding dimension mismatch")
    return vectors


def test_model_configuration(
    config: LLMConfig,
    settings: Settings | None = None,
) -> str:
    if config.capability == "embedding":
        vectors = embed_texts(config, ["连接测试"], settings)
        return f"{len(vectors[0])} dimensions" if vectors else ""
    client = build_openai_client(config, settings)
    completion = client.chat.completions.create(
        model=config.model_name,
        messages=[{"role": "user", "content": "只回复 OK"}],
        temperature=0,
        max_tokens=8,
        **completion_overrides(config),
    )
    return (completion.choices[0].message.content or "").strip()
