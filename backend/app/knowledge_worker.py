from __future__ import annotations

import json
import signal
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.db import get_session_factory
from backend.app.models import AIModelBinding, LLMConfig
from backend.app.modules.ai_models.service import (
    embed_texts,
    embedding_model_version,
    resolve_model_chain,
)
from backend.app.modules.knowledge.models import KnowledgeImport, KnowledgeIndexJob
from backend.app.modules.knowledge.service import (
    claim_next_index_job,
    complete_index_job,
    fail_index_job,
    store_knowledge_embedding,
)
from backend.app.modules.knowledge.v2_service import store_v2_embedding

HEARTBEAT_PATH = Path("/tmp/avatar-knowledge-worker-heartbeat")
_running = True


def _stop(_: int, __: Any) -> None:
    global _running
    _running = False


def _candidate_models(db: Session, job: KnowledgeIndexJob) -> list[LLMConfig]:
    if job.model_config_id:
        selected = db.get(LLMConfig, job.model_config_id)
        if (
            selected
            and selected.is_active
            and selected.capability == "embedding"
            and embedding_model_version(selected) == job.target_model_version
        ):
            return [selected]
        return []
    chain = resolve_model_chain(db, scene="embedding", capability="embedding")
    return [
        item
        for item in (chain.primary, chain.fallback)
        if item is not None and embedding_model_version(item) == job.target_model_version
    ]


def process_index_job(
    db: Session,
    job: KnowledgeIndexJob,
    settings: Settings | None = None,
) -> None:
    configured = settings or get_settings()
    knowledge_import = db.get(KnowledgeImport, job.import_id)
    if not knowledge_import or knowledge_import.status != "published":
        fail_index_job(
            db,
            job_id=job.id,
            error_message="published import not found",
            retryable=False,
            settings=configured,
        )
        return
    if knowledge_import.schema_version == "avatar-knowledge/v2":
        targets = [
            *[
                ("slice", item, f"{item.title}\n{item.summary}\n{item.structured_content}")
                for item in knowledge_import.slices
                if item.status == "published"
            ],
            *[
                (
                    "product",
                    item,
                    f"{item.title}\n{item.summary}\n{json.dumps(item.payload, ensure_ascii=False)}",
                )
                for item in knowledge_import.products
                if item.status == "published"
            ],
            *[
                ("style", item, f"{item.title}\n{item.content}")
                for item in knowledge_import.style_entries
                if item.status == "published"
            ],
        ]
    else:
        targets = [
            ("unit", unit, f"{unit.title}\n{unit.content}")
            for unit in knowledge_import.units
            if unit.status == "published"
        ]
    if not targets:
        fail_index_job(
            db,
            job_id=job.id,
            error_message="published index targets not found",
            retryable=False,
            settings=configured,
        )
        return
    texts = [target[2] for target in targets]
    candidates = _candidate_models(db, job)
    if not candidates:
        fail_index_job(
            db,
            job_id=job.id,
            error_message="embedding model unavailable",
            retryable=False,
            settings=configured,
        )
        return

    last_error = "embedding request failed"
    for config in candidates:
        try:
            binding = db.query(AIModelBinding).filter_by(scene="embedding").one_or_none()
            activate = bool(
                binding
                and binding.primary_model_id == config.id
                and binding.pending_primary_model_id != config.id
            )
            vectors: list[list[float]] = []
            batch_size = max(1, min(configured.knowledge_embedding_batch_size, 128))
            for start in range(0, len(texts), batch_size):
                vectors.extend(embed_texts(config, texts[start : start + batch_size], configured))
            if len(vectors) != len(targets):
                raise ValueError("embedding result count mismatch")
            for (kind, owner, _), vector in zip(targets, vectors, strict=True):
                if kind == "unit":
                    store_knowledge_embedding(
                        db,
                        unit_id=owner.id,
                        embedding=vector,
                        model_name=config.model_name,
                        model_version=job.target_model_version,
                        activate=activate,
                    )
                else:
                    store_v2_embedding(
                        db,
                        kind=kind,
                        owner_id=owner.id,
                        embedding=vector,
                        model_name=config.model_name,
                        model_version=job.target_model_version,
                        activate=activate,
                    )
            complete_index_job(db, job_id=job.id)
            return
        except Exception as exc:
            last_error = exc.__class__.__name__
    fail_index_job(
        db,
        job_id=job.id,
        error_message=last_error,
        retryable=True,
        settings=configured,
    )


def run_worker() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    session_factory = get_session_factory()
    configured = get_settings()
    while _running:
        HEARTBEAT_PATH.touch()
        with session_factory() as db:
            job = claim_next_index_job(db, configured)
            if job:
                process_index_job(db, job)
                continue
        time.sleep(2)
    HEARTBEAT_PATH.touch()


if __name__ == "__main__":
    run_worker()
