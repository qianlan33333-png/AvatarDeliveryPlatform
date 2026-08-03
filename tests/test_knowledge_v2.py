from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db import get_session_factory
from backend.app.main import app
from backend.app.modules.knowledge.models import (
    KnowledgeProduct,
    KnowledgeSlice,
    QAEntry,
    StyleEntry,
)
from backend.app.modules.knowledge.service import (
    KnowledgeConflictError,
    KnowledgeValidationError,
    create_knowledge_source,
)
from backend.app.modules.knowledge.v2_markdown import export_v2_markdown
from backend.app.modules.knowledge.v2_retrieval import retrieve_strict_qa_v2, retrieve_v2_knowledge
from backend.app.modules.knowledge.v2_service import (
    create_qa_entry,
    create_v2_source,
    import_v2_markdown,
    publish_v2_import,
    review_v2_item,
    set_qa_status,
)


def _cleaned(document: str, *, confirmation: str = "confirmed") -> str:
    payload = f'''```yaml
cleaned_schema: 2
processor: "test-agent"
processor_version: "2.0.0"
slices:
  - local_id: "KS-001"
    dimension: "values"
    title: "先解决真实问题"
    summary: "内容服务于真实问题"
    original_excerpt: "内容要解决真实问题"
    structured_content: "判断内容价值时，先看它是否解决真实问题。"
    usage_context: "内容选题"
    golden_sentence: "内容要解决真实问题"
    content_type: "cognition"
    primary_domain: "biz_ops"
    secondary_domains: ["opc_growth"]
    topic_tags: ["内容"]
    industry_tags: []
    audience_tags: ["创业者"]
    source_evidence: "内容要解决真实问题"
    confirmation: "{confirmation}"
products:
  - local_id: "KP-001"
    type: "judgement_card"
    title: "内容价值判断卡"
    summary: "先看真实问题"
    payload: {{"verdict":"内容必须解决真实问题"}}
    source_slice_ids: ["KS-001"]
    primary_domain: "biz_ops"
    secondary_domains: []
    source_evidence: "内容要解决真实问题"
    confirmation: "confirmed"
style_entries:
  - local_id: "ST-001"
    type: "rule"
    title: "表达直接"
    content: "先给判断，再解释原因。"
    channels: ["wechat"]
    audiences: ["创业者"]
    purposes: ["问答"]
    source_evidence: "内容要解决真实问题"
    confirmation: "confirmed"
```'''
    start = document.index("```yaml")
    end = document.index("```", start + 3) + 3
    return document[:start] + payload + document[end:]


def test_v2_source_starts_isolated_and_import_is_idempotent() -> None:
    with get_session_factory()() as db:
        source = create_v2_source(db, title="内容素材", raw_content="内容要解决真实问题")
        assert source.schema_version == "avatar-knowledge/v2"
        document = _cleaned(export_v2_markdown(source.versions[0]))
        first = import_v2_markdown(db, markdown=document)
        second = import_v2_markdown(db, markdown=document)
        assert first.created is True
        assert second.created is False
        assert db.scalar(select(KnowledgeSlice)).dimension == "values"
        assert db.scalar(select(KnowledgeProduct)).product_type == "judgement_card"
        assert db.scalar(select(StyleEntry)).entry_type == "rule"


def test_v2_review_and_atomic_publish_gate() -> None:
    with get_session_factory()() as db:
        source = create_v2_source(db, title="内容素材", raw_content="内容要解决真实问题")
        imported = import_v2_markdown(db, markdown=_cleaned(export_v2_markdown(source.versions[0])))
        with pytest.raises(KnowledgeConflictError, match="所有条目"):
            publish_v2_import(db, import_id=imported.knowledge_import.id)
        for kind, item in (
            ("slice", imported.knowledge_import.slices[0]),
            ("product", imported.knowledge_import.products[0]),
            ("style", imported.knowledge_import.style_entries[0]),
        ):
            review_v2_item(db, item_kind=kind, item_id=item.id, decision="approved")
        published = publish_v2_import(db, import_id=imported.knowledge_import.id)
        assert published.status == "published"
        assert all(
            item.status == "published"
            for item in [*published.slices, *published.products, *published.style_entries]
        )


def test_v2_rejects_unconfirmed_and_invalid_domain() -> None:
    with get_session_factory()() as db:
        source = create_v2_source(db, title="内容素材", raw_content="内容要解决真实问题")
        document = _cleaned(
            export_v2_markdown(source.versions[0]), confirmation="needs_confirmation"
        )
        imported = import_v2_markdown(db, markdown=document)
        with pytest.raises(KnowledgeConflictError, match="待确认"):
            review_v2_item(
                db,
                item_kind="slice",
                item_id=imported.knowledge_import.slices[0].id,
                decision="approved",
            )
        invalid = document.replace('primary_domain: "biz_ops"', 'primary_domain: "unknown"', 1)
        with pytest.raises(KnowledgeValidationError, match="封闭枚举"):
            import_v2_markdown(db, markdown=invalid)


def test_v2_qa_has_fixed_answer_and_six_image_limit() -> None:
    with get_session_factory()() as db:
        qa = create_qa_entry(
            db,
            question="怎么报名？",
            answer="请联系运营报名。",
            aliases=["如何报名"],
            images=[{"url": "https://cdn.example.com/signup.png", "alt_text": "报名二维码"}],
        )
        assert qa.status == "draft" and len(qa.assets) == 1
        set_qa_status(db, entry_id=qa.id, status="approved")
        published = set_qa_status(db, entry_id=qa.id, status="published")
        assert published.fixed_answer == "请联系运营报名。"
        assert published.assets[0].status == "published"
        too_many = [{"url": f"https://cdn.example.com/{index}.png"} for index in range(7)]
        with pytest.raises(KnowledgeValidationError, match="最多 6"):
            create_qa_entry(db, question="图片？", answer="见图", images=too_many)


def test_v2_qa_starts_empty_even_when_old_units_exist() -> None:
    with get_session_factory()() as db:
        assert db.scalar(select(QAEntry)) is None


def test_v2_retrieval_respects_dimensions_and_style_separation() -> None:
    from backend.app.models import User

    with get_session_factory()() as db:
        user = User(nickname="V2 检索用户")
        db.add(user)
        db.flush()
        source = create_knowledge_source(
            db,
            title="公开 V2 素材",
            source_type="material",
            visibility="public",
            raw_content="内容要解决真实问题",
            schema_version="avatar-knowledge/v2",
        )
        imported = import_v2_markdown(db, markdown=_cleaned(export_v2_markdown(source.versions[0])))
        for kind, item in (
            ("slice", imported.knowledge_import.slices[0]),
            ("product", imported.knowledge_import.products[0]),
            ("style", imported.knowledge_import.style_entries[0]),
        ):
            review_v2_item(db, item_kind=kind, item_id=item.id, decision="approved")
        publish_v2_import(db, import_id=imported.knowledge_import.id)
        internal_source = create_v2_source(
            db, title="内部风格素材", raw_content="内容要解决真实问题"
        )
        internal_import = import_v2_markdown(
            db, markdown=_cleaned(export_v2_markdown(internal_source.versions[0]))
        ).knowledge_import
        for kind, item in (
            ("slice", internal_import.slices[0]),
            ("product", internal_import.products[0]),
            ("style", internal_import.style_entries[0]),
        ):
            review_v2_item(db, item_kind=kind, item_id=item.id, decision="approved")
        publish_v2_import(db, import_id=internal_import.id)
        qa_result = retrieve_v2_knowledge(
            db,
            user_id=user.id,
            query="内容问题",
            mode="qa",
            dimensions=["values"],
            domains=["biz_ops"],
        )
        copy_result = retrieve_v2_knowledge(
            db,
            user_id=user.id,
            query="内容问题",
            mode="copywriting",
            dimensions=["values"],
            domains=["biz_ops"],
        )
        assert any(item.kind == "slice" and item.type == "values" for item in qa_result.references)
        assert all(item.kind != "style" for item in qa_result.references)
        assert any(item.kind == "style" for item in copy_result.references)
        internal_ids = {
            item.id
            for item in [
                *internal_import.slices,
                *internal_import.products,
                *internal_import.style_entries,
            ]
        }
        assert not (internal_ids & {item.id for item in copy_result.references})


def test_v2_strict_qa_bypasses_model_with_stored_images() -> None:
    from backend.app.models import User

    with get_session_factory()() as db:
        user = User(nickname="纯 QA 用户")
        db.add(user)
        db.flush()
        qa = create_qa_entry(
            db,
            question="课程怎么报名？",
            answer="请联系运营报名。",
            aliases=["如何报名"],
            visibility="public",
            images=[{"url": "https://cdn.example.com/signup.png", "alt_text": "报名图"}],
        )
        set_qa_status(db, entry_id=qa.id, status="approved")
        set_qa_status(db, entry_id=qa.id, status="published")
        result = retrieve_strict_qa_v2(db, user_id=user.id, query="如何报名")
        assert result is not None
        assert result.answer == "请联系运营报名。"
        assert result.images[0]["url"] == "https://cdn.example.com/signup.png"


def test_v2_admin_hides_v1_and_exposes_three_clear_entries() -> None:
    with get_session_factory()() as db:
        create_knowledge_source(
            db,
            title="旧 V1 不应展示",
            source_type="notes",
            visibility="public",
            raw_content="旧内容",
        )
        create_v2_source(db, title="新版素材", raw_content="新版原文")
    with TestClient(app) as client:
        login = client.post(
            "/admin/login",
            data={
                "username": "admin",
                "password": "test-admin-password",
                "next_url": "/admin/knowledge",
            },
        )
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login.text)
        assert csrf
        listing = client.get("/admin/knowledge")
        form = client.get("/admin/knowledge/new")
        slices = client.get("/admin/knowledge-slices")
        qa = client.get("/admin/qa-library")
    assert "新版素材" in listing.text and "旧 V1 不应展示" not in listing.text
    assert (
        "来源分类" not in listing.text and "素材名称" in listing.text and "清洗状态" in listing.text
    )
    assert 'name="source_type"' not in form.text and 'name="visibility"' not in form.text
    assert "底层心法" in slices.text and "金句语录" in slices.text
    assert "固定答案" in qa.text and "旧 QA" in qa.text
