from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

from sqlalchemy import String
from sqlalchemy.exc import SQLAlchemyError

from backend.app.modules.knowledge import service
from backend.app.modules.knowledge.models import KnowledgeUnit


class _FakeSession:
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    def begin_nested(self):
        return nullcontext()

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        if "SELECT" in str(statement):
            return self.rows
        return []


def _unit(*, unit_id: str, unit_type: str, question: str = "") -> KnowledgeUnit:
    return KnowledgeUnit(
        id=unit_id,
        source_id=f"source-{unit_id}",
        source_version_id=f"version-{unit_id}",
        import_id=f"import-{unit_id}",
        local_id=f"local-{unit_id}",
        unit_type=unit_type,
        title=question or f"title-{unit_id}",
        content=f"content-{unit_id}",
        standard_question=question,
        standard_answer=f"answer-{unit_id}" if question else "",
        aliases=[],
        keywords=[],
        channels=[],
        source_evidence=f"evidence-{unit_id}",
        confirmation="confirmed",
        visibility="public",
        course_ids=[],
        status="published",
        assets=[],
    )


def test_postgres_rank_queries_bind_untrusted_values_and_filter_course_json() -> None:
    untrusted_query = "内容%_'); DROP TABLE knowledge_units; --"
    untrusted_course_id = "course-1'); DELETE FROM users; --"
    untrusted_model = "embedding'; DROP TABLE knowledge_embeddings; --"

    lexical, lexical_params = service._postgres_lexical_query(
        authorized_course_ids=[untrusted_course_id],
        allowed_types={"fact", "faq"},
        query=untrusted_query,
        limit=30,
    )
    lexical_sql = str(lexical)

    assert "jsonb_array_elements_text" in lexical_sql
    assert "permitted_course.course_id IN" in lexical_sql
    assert "similarity(" in lexical_sql
    assert " % :search_query" in lexical_sql
    assert untrusted_query not in lexical_sql
    assert untrusted_course_id not in lexical_sql
    assert lexical_params["search_query"] == untrusted_query
    assert lexical_params["contains_query"] == "%内容\\%\\_'); DROP TABLE knowledge\\_units; --%"
    assert lexical_params["authorized_course_ids"] == [untrusted_course_id]
    assert lexical_params["result_limit"] == 30

    vector = [1.0, *([0.0] * 1023)]
    vector_query, vector_params = service._postgres_vector_query(
        authorized_course_ids=[untrusted_course_id],
        allowed_types={"fact"},
        query_embedding=vector,
        embedding_model_name=untrusted_model,
        limit=30,
    )
    vector_sql = str(vector_query)

    assert "JOIN knowledge_units AS ku" in vector_sql
    assert "jsonb_array_elements_text" in vector_sql
    assert "ke.embedding <=> CAST(:query_embedding AS vector)" in vector_sql
    assert untrusted_course_id not in vector_sql
    assert untrusted_model not in vector_sql
    assert vector_params["authorized_course_ids"] == [untrusted_course_id]
    assert vector_params["embedding_model_name"] == untrusted_model
    assert vector_params["query_embedding"].startswith("[1,0,0,")


def test_postgres_strict_qa_query_binds_aliases_and_applies_authorized_scope() -> None:
    untrusted_query = "课程%_'); DROP TABLE knowledge_units; --"
    untrusted_course_id = "course-qa'); DELETE FROM users; --"

    statement, params = service._postgres_strict_qa_query(
        authorized_course_ids=[untrusted_course_id],
        query=untrusted_query,
    )
    sql = str(statement)

    assert "JOIN knowledge_sources AS ks" in sql
    assert "ks.source_type = 'pure_qa'" in sql
    assert "ks.status = 'published'" in sql
    assert "ku.status = 'published'" in sql
    assert "ku.visibility != 'internal'" in sql
    assert "jsonb_array_elements_text" in sql
    assert "similarity(qa_alias.value, :search_query)" in sql
    assert untrusted_query not in sql
    assert untrusted_course_id not in sql
    assert params["search_query"] == untrusted_query
    assert params["authorized_course_ids"] == [untrusted_course_id]
    assert params["allowed_types"] == ["qa"]

    empty_scope, empty_params = service._postgres_strict_qa_query(
        authorized_course_ids=[],
        query="公开问题",
    )
    assert isinstance(empty_scope._bindparams["authorized_course_ids"].type, String)
    assert isinstance(empty_scope._bindparams["allowed_types"].type, String)
    assert empty_params["authorized_course_ids"] == []


def test_postgres_vector_rank_sets_iterative_scan_before_bound_query() -> None:
    db = _FakeSession(
        rows=[
            SimpleNamespace(unit_id="unit-a", score=0.91),
            SimpleNamespace(unit_id="unit-a", score=0.90),
            SimpleNamespace(unit_id="unit-b", score=0.80),
        ]
    )

    ranked = service._postgres_vector_rank(
        db,
        authorized_course_ids=["course-a"],
        allowed_types={"fact"},
        query_embedding=[1.0, *([0.0] * 1023)],
        embedding_model_name="embedding-model",
        limit=30,
    )

    assert ranked == [("unit-a", 0.91), ("unit-b", 0.80)]
    assert db.executed[0][0].strip() == "SET LOCAL hnsw.iterative_scan = strict_order"
    assert "<=> CAST(:query_embedding AS vector)" in db.executed[1][0]
    assert db.executed[1][1]["authorized_course_ids"] == ["course-a"]
    assert db.executed[1][1]["embedding_model_name"] == "embedding-model"


def test_postgres_retrieval_resolves_access_before_rank_and_degrades_to_lexical(
    monkeypatch,
) -> None:
    events: list[tuple[str, object]] = []
    boundary = _unit(unit_id="boundary", unit_type="answer_boundary")
    fact = _unit(unit_id="fact", unit_type="fact")

    def effective_course_ids(db, *, user_id, now):
        events.append(("course_access", user_id))
        return {"course-a"}

    def boundary_ids(db, *, authorized_course_ids):
        events.append(("boundaries", set(authorized_course_ids)))
        return [boundary.id]

    def lexical_rank(db, *, authorized_course_ids, allowed_types, query, limit):
        events.append(("lexical", set(authorized_course_ids)))
        return [(fact.id, 0.95)]

    def vector_rank(db, **kwargs):
        events.append(("vector", set(kwargs["authorized_course_ids"])))
        raise SQLAlchemyError("embedding unavailable")

    def load_units(db, unit_ids):
        events.append(("load", tuple(unit_ids)))
        return {boundary.id: boundary, fact.id: fact}

    monkeypatch.setattr(service, "_effective_course_ids", effective_course_ids)
    monkeypatch.setattr(service, "_postgres_authorized_boundary_ids", boundary_ids)
    monkeypatch.setattr(service, "_postgres_lexical_rank", lexical_rank)
    monkeypatch.setattr(service, "_postgres_vector_rank", vector_rank)
    monkeypatch.setattr(service, "_load_knowledge_units_by_id", load_units)

    result = service.retrieve_knowledge(
        _FakeSession(),
        user_id="user-a",
        query="怎么写内容",
        mode="copywriting",
        query_embedding=[1.0, *([0.0] * 1023)],
        embedding_model_name="embedding-model",
    )

    assert [name for name, _ in events] == [
        "course_access",
        "boundaries",
        "lexical",
        "vector",
        "load",
    ]
    assert all(value == {"course-a"} for _, value in events[1:4])
    assert [unit.id for unit in result.units] == [boundary.id, fact.id]
    assert result.lexical_candidate_count == 1
    assert result.vector_candidate_count == 0
    assert result.used_vector is False
    assert result.degraded is True


def test_sqlite_retrieval_keeps_python_fallback(monkeypatch) -> None:
    db = _FakeSession()
    db.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    fact = _unit(unit_id="fact", unit_type="fact")

    monkeypatch.setattr(service, "_eligible_published_units", lambda *args, **kwargs: [fact])

    def unexpected_postgres_call(*args, **kwargs):
        raise AssertionError("SQLite fallback must not execute PostgreSQL retrieval")

    monkeypatch.setattr(
        service,
        "_postgres_authorized_boundary_ids",
        unexpected_postgres_call,
    )
    monkeypatch.setattr(service, "_postgres_lexical_rank", unexpected_postgres_call)
    monkeypatch.setattr(service, "_postgres_vector_rank", unexpected_postgres_call)

    result = service.retrieve_knowledge(
        db,
        user_id="user-a",
        query="content-fact",
        mode="copywriting",
    )

    assert [unit.id for unit in result.units] == [fact.id]
    assert result.used_vector is False
    assert result.degraded is True


def test_strict_qa_postgres_path_is_candidate_bounded(monkeypatch) -> None:
    events: list[str] = []
    qa_unit = _unit(unit_id="qa", unit_type="qa", question="课程适合谁")

    def effective_course_ids(db, *, user_id, now):
        events.append("course_access")
        return {"course-a"}

    def candidate_ids(db, *, authorized_course_ids, query, limit=30):
        events.append("candidate_ids")
        assert authorized_course_ids == {"course-a"}
        return [qa_unit.id]

    def load_units(db, unit_ids):
        events.append("load")
        assert unit_ids == [qa_unit.id]
        return {qa_unit.id: qa_unit}

    monkeypatch.setattr(service, "_effective_course_ids", effective_course_ids)
    monkeypatch.setattr(service, "_postgres_strict_qa_candidate_ids", candidate_ids)
    monkeypatch.setattr(service, "_load_knowledge_units_by_id", load_units)
    monkeypatch.setattr(
        service,
        "_eligible_published_units",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("PostgreSQL strict QA must not hydrate the full corpus")
        ),
    )

    result = service.retrieve_strict_qa(
        _FakeSession(),
        user_id="user-a",
        query="课程适合谁",
    )

    assert events == ["course_access", "candidate_ids", "load"]
    assert result is not None
    assert result.strict_answer is True
    assert [unit.id for unit in result.units] == [qa_unit.id]
