from __future__ import annotations

from backend.app.config import get_settings
from backend.app.db import get_session_factory
from backend.app.knowledge_worker import process_index_job
from backend.app.models import AIModelBinding, LLMConfig
from backend.app.modules.ai_models.service import embedding_model_version
from backend.app.modules.knowledge.models import (
    KnowledgeEmbedding,
    KnowledgeImport,
    KnowledgeIndexJob,
    KnowledgeSource,
    KnowledgeSourceVersion,
    KnowledgeUnit,
)
from backend.app.security import encrypt_value


def test_dedicated_worker_embeds_and_completes_published_job(monkeypatch) -> None:
    import backend.app.knowledge_worker as worker

    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as db:
        source = KnowledgeSource(
            title="索引语料",
            source_type="notes",
            visibility="public",
            status="published",
        )
        db.add(source)
        db.flush()
        version = KnowledgeSourceVersion(
            source_id=source.id,
            version_number=1,
            source_sha256="a" * 64,
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
            idempotency_key="b" * 64,
            raw_markdown="# test",
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
            title="可靠事实",
            content="这是一条可靠事实。",
            source_evidence="可靠事实",
            confirmation="confirmed",
            visibility="public",
            status="published",
        )
        model = LLMConfig(
            name="测试向量模型",
            provider="custom",
            capability="embedding",
            base_url="https://embedding.example.com/v1",
            model_name="embedding-test",
            embedding_dimension=1024,
            api_key_ciphertext=encrypt_value(
                "secret",
                purpose="llm-api-key",
                key_material=settings.llm_encryption_key,
                settings=settings,
            ),
            is_active=True,
        )
        db.add_all([unit, model])
        db.flush()
        db.add(AIModelBinding(scene="embedding", primary_model_id=model.id))
        job = KnowledgeIndexJob(
            source_id=source.id,
            source_version_id=version.id,
            import_id=knowledge_import.id,
            model_config_id=model.id,
            target_model_version=embedding_model_version(model),
            job_key="c" * 64,
            status="running",
            attempts=1,
        )
        db.add(job)
        db.commit()

        monkeypatch.setattr(
            worker,
            "embed_texts",
            lambda config, texts, configured: [[1.0, *([0.0] * 1023)] for _ in texts],
        )
        process_index_job(db, job, settings)

        db.refresh(job)
        stored = db.query(KnowledgeEmbedding).one()
        assert job.status == "completed"
        assert stored.unit_id == unit.id
        assert stored.model_name == "embedding-test"
        assert len(stored.embedding) == 1024
