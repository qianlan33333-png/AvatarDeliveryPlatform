from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

import backend.app.knowledge_worker as worker
from backend.app.config import get_settings
from backend.app.db import get_session_factory
from backend.app.main import app
from backend.app.models import AIModelBinding, LLMConfig
from backend.app.modules.ai_models.service import (
    embedding_model_version,
    embedding_model_version_from_fields,
    resolve_model_chain,
)
from backend.app.modules.knowledge.models import (
    KnowledgeEmbedding,
    KnowledgeImport,
    KnowledgeIndexJob,
    KnowledgeSource,
    KnowledgeSourceVersion,
    KnowledgeUnit,
)
from backend.app.modules.knowledge.service import (
    claim_next_index_job,
    queue_published_knowledge_for_reindex,
    store_knowledge_embedding,
)
from backend.app.security import encrypt_value


def _embedding_model(*, name: str, model_name: str) -> LLMConfig:
    settings = get_settings()
    return LLMConfig(
        name=name,
        provider="custom",
        capability="embedding",
        base_url=f"https://{model_name}.example.com/v1",
        model_name=model_name,
        embedding_dimension=1024,
        api_key_ciphertext=encrypt_value(
            f"secret-{model_name}",
            purpose="llm-api-key",
            key_material=settings.llm_encryption_key,
            settings=settings,
        ),
        is_active=True,
    )


def _published_import(db, *, suffix: str) -> tuple[KnowledgeImport, KnowledgeUnit]:
    source = KnowledgeSource(
        title=f"索引语料-{suffix}",
        source_type="notes",
        visibility="public",
        status="published",
    )
    db.add(source)
    db.flush()
    version = KnowledgeSourceVersion(
        source_id=source.id,
        version_number=1,
        source_sha256=(suffix * 64)[:64],
        title=source.title,
        source_type=source.source_type,
        visibility="public",
        status="published",
    )
    db.add(version)
    db.flush()
    knowledge_import = KnowledgeImport(
        source_id=source.id,
        source_version_id=version.id,
        source_version_number=1,
        source_sha256=version.source_sha256,
        idempotency_key=(f"import-{suffix}" * 16)[:64],
        raw_markdown=f"# test {suffix}",
        status="published",
    )
    db.add(knowledge_import)
    db.flush()
    unit = KnowledgeUnit(
        source_id=source.id,
        source_version_id=version.id,
        import_id=knowledge_import.id,
        local_id="KU-001",
        unit_type="fact",
        title=f"可靠事实-{suffix}",
        content=f"这是一条可靠事实-{suffix}。",
        source_evidence=f"可靠事实-{suffix}",
        confirmation="confirmed",
        visibility="public",
        status="published",
    )
    db.add(unit)
    db.flush()
    return knowledge_import, unit


def _generation_fixture(db, *, import_count: int = 2):
    old_model = _embedding_model(name="旧向量", model_name="embedding-old")
    new_model = _embedding_model(name="新向量", model_name="embedding-new")
    db.add_all([old_model, new_model])
    db.flush()
    binding = AIModelBinding(
        scene="embedding",
        primary_model_id=old_model.id,
        pending_primary_model_id=new_model.id,
    )
    db.add(binding)
    imports_and_units = [
        _published_import(db, suffix=str(index + 1)) for index in range(import_count)
    ]
    db.commit()
    for _, unit in imports_and_units:
        store_knowledge_embedding(
            db,
            unit_id=unit.id,
            embedding=[1.0, *([0.0] * 1023)],
            model_name=old_model.model_name,
            model_version=embedding_model_version(old_model),
        )
    created = queue_published_knowledge_for_reindex(db, model_config_id=new_model.id)
    assert created == import_count
    jobs = list(
        db.scalars(
            select(KnowledgeIndexJob)
            .where(KnowledgeIndexJob.model_config_id == new_model.id)
            .order_by(KnowledgeIndexJob.created_at, KnowledgeIndexJob.id)
        )
    )
    return old_model, new_model, binding, imports_and_units, jobs


def _run_job(db, job: KnowledgeIndexJob, monkeypatch, *, fail: bool = False, settings=None):
    job.status = "running"
    job.attempts = max(1, job.attempts)
    job.started_at = datetime.now(UTC)
    db.commit()
    if fail:
        monkeypatch.setattr(
            worker,
            "embed_texts",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
        )
    else:
        monkeypatch.setattr(
            worker,
            "embed_texts",
            lambda config, texts, configured: [
                [0.0, 1.0, *([0.0] * 1022)] for _ in texts
            ],
        )
    worker.process_index_job(db, job, settings or get_settings())


def test_pending_generation_keeps_old_primary_until_all_imports_complete(monkeypatch) -> None:
    with get_session_factory()() as db:
        old_model, new_model, binding, _, jobs = _generation_fixture(db)
        assert resolve_model_chain(
            db, scene="embedding", capability="embedding"
        ).primary.id == old_model.id

        _run_job(db, jobs[0], monkeypatch)
        db.refresh(binding)
        assert binding.primary_model_id == old_model.id
        assert binding.pending_primary_model_id == new_model.id
        first_target = list(
            db.scalars(
                select(KnowledgeEmbedding).where(
                    KnowledgeEmbedding.model_version == embedding_model_version(new_model)
                )
            )
        )
        assert first_target and all(not item.is_active for item in first_target)

        _run_job(db, jobs[1], monkeypatch)
        db.refresh(binding)
        assert binding.primary_model_id == new_model.id
        assert binding.pending_primary_model_id is None
        assert resolve_model_chain(
            db, scene="embedding", capability="embedding"
        ).primary.id == new_model.id
        old_vectors = list(
            db.scalars(
                select(KnowledgeEmbedding).where(
                    KnowledgeEmbedding.model_version == embedding_model_version(old_model)
                )
            )
        )
        new_vectors = list(
            db.scalars(
                select(KnowledgeEmbedding).where(
                    KnowledgeEmbedding.model_version == embedding_model_version(new_model)
                )
            )
        )
        assert old_vectors and all(not item.is_active for item in old_vectors)
        assert len(new_vectors) == 2 and all(item.is_active for item in new_vectors)


def test_failed_pending_generation_never_switches_primary(monkeypatch) -> None:
    settings = get_settings().model_copy(update={"knowledge_index_max_attempts": 1})
    with get_session_factory()() as db:
        old_model, new_model, binding, _, jobs = _generation_fixture(db)
        _run_job(db, jobs[0], monkeypatch, settings=settings)
        _run_job(db, jobs[1], monkeypatch, fail=True, settings=settings)

        db.refresh(binding)
        db.refresh(jobs[1])
        assert jobs[1].status == "failed"
        assert binding.primary_model_id == old_model.id
        assert binding.pending_primary_model_id == new_model.id
        assert resolve_model_chain(
            db, scene="embedding", capability="embedding"
        ).primary.id == old_model.id


def test_embedding_failure_backs_off_and_stale_running_job_is_reclaimed(monkeypatch) -> None:
    settings = get_settings().model_copy(
        update={
            "knowledge_index_max_attempts": 3,
            "knowledge_index_retry_base_seconds": 60,
            "knowledge_index_running_timeout_seconds": 60,
        }
    )
    with get_session_factory()() as db:
        old_model, new_model, binding, _, jobs = _generation_fixture(db, import_count=1)
        job = claim_next_index_job(db, settings)
        assert job is not None and job.id == jobs[0].id and job.attempts == 1

        _run_job(db, job, monkeypatch, fail=True, settings=settings)
        db.refresh(job)
        assert job.status == "pending"
        assert job.next_attempt_at is not None
        assert claim_next_index_job(db, settings) is None
        db.refresh(binding)
        assert binding.primary_model_id == old_model.id
        assert binding.pending_primary_model_id == new_model.id

        job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        reclaimed = claim_next_index_job(db, settings)
        assert reclaimed is not None and reclaimed.id == job.id and reclaimed.attempts == 2
        reclaimed.started_at = datetime.now(UTC) - timedelta(seconds=120)
        db.commit()
        reclaimed_after_crash = claim_next_index_job(db, settings)
        assert reclaimed_after_crash is not None
        assert reclaimed_after_crash.id == job.id
        assert reclaimed_after_crash.attempts == 3

        _run_job(db, reclaimed_after_crash, monkeypatch, fail=True, settings=settings)
        db.refresh(job)
        db.refresh(binding)
        assert job.status == "failed"
        assert binding.primary_model_id == old_model.id
        assert binding.pending_primary_model_id == new_model.id


def test_embedding_identity_ignores_api_key_but_changes_for_vector_identity() -> None:
    model = _embedding_model(name="稳定代次", model_name="embedding-stable")
    original = embedding_model_version(model)
    model.api_key_ciphertext = "rotated-secret-ciphertext"
    assert embedding_model_version(model) == original
    changed = embedding_model_version_from_fields(
        provider=model.provider,
        base_url=model.base_url,
        model_name="embedding-next",
        embedding_dimension=model.embedding_dimension,
    )
    assert changed != original


def test_editing_bound_embedding_identity_creates_pending_generation() -> None:
    with get_session_factory()() as db:
        old_model = _embedding_model(name="后台向量", model_name="embedding-old")
        db.add(old_model)
        db.flush()
        binding = AIModelBinding(scene="embedding", primary_model_id=old_model.id)
        db.add(binding)
        _published_import(db, suffix="admin")
        db.commit()
        old_model_id = old_model.id

    with TestClient(app) as client:
        login = client.post(
            "/admin/login",
            data={"username": "admin", "password": "test-admin-password"},
        )
        match = re.search(r'name="csrf_token" value="([^"]+)"', login.text)
        assert match
        response = client.post(
            "/admin/llm-config",
            data={
                "csrf_token": match.group(1),
                "config_id": old_model_id,
                "name": "后台向量",
                "provider": "custom",
                "capability": "embedding",
                "base_url": "https://embedding-new.example.com/v1",
                "model_name": "embedding-new",
                "embedding_dimension": "1024",
                "temperature": "0.5",
                "is_active": "true",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "notice=embedding-rebuilding" in response.headers["location"]

    with get_session_factory()() as db:
        binding = db.scalar(select(AIModelBinding).where(AIModelBinding.scene == "embedding"))
        assert binding is not None
        assert binding.primary_model_id == old_model_id
        assert binding.pending_primary_model_id not in {None, old_model_id}
        old_model = db.get(LLMConfig, old_model_id)
        pending = db.get(LLMConfig, binding.pending_primary_model_id)
        assert old_model is not None and old_model.model_name == "embedding-old"
        assert pending is not None and pending.model_name == "embedding-new"
        job = db.scalar(
            select(KnowledgeIndexJob).where(
                KnowledgeIndexJob.model_config_id == pending.id
            )
        )
        assert job is not None
        assert job.target_model_version == embedding_model_version(pending)


def test_admin_binding_switch_stages_pending_and_shows_rebuilding() -> None:
    with get_session_factory()() as db:
        old_model = _embedding_model(name="绑定旧向量", model_name="binding-old")
        new_model = _embedding_model(name="绑定新向量", model_name="binding-new")
        db.add_all([old_model, new_model])
        db.flush()
        db.add(AIModelBinding(scene="embedding", primary_model_id=old_model.id))
        _published_import(db, suffix="binding")
        db.commit()
        old_model_id = old_model.id
        new_model_id = new_model.id

    with TestClient(app) as client:
        login = client.post(
            "/admin/login",
            data={"username": "admin", "password": "test-admin-password"},
        )
        match = re.search(r'name="csrf_token" value="([^"]+)"', login.text)
        assert match
        saved = client.post(
            "/admin/llm-config/bindings",
            data={
                "csrf_token": match.group(1),
                "embedding_primary": new_model_id,
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
        page = client.get("/admin/llm-config")
        assert "重建中" in page.text
        assert "当前模型继续服务" in page.text

    with get_session_factory()() as db:
        binding = db.scalar(select(AIModelBinding).where(AIModelBinding.scene == "embedding"))
        assert binding is not None
        assert binding.primary_model_id == old_model_id
        assert binding.pending_primary_model_id == new_model_id
        assert resolve_model_chain(
            db, scene="embedding", capability="embedding"
        ).primary.id == old_model_id
        jobs = list(
            db.scalars(
                select(KnowledgeIndexJob).where(
                    KnowledgeIndexJob.model_config_id == new_model_id
                )
            )
        )
        assert len(jobs) == 1
