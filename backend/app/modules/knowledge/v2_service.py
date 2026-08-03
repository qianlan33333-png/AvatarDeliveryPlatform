from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import Course
from backend.app.modules.knowledge.markdown import (
    KnowledgeMarkdownError,
    compute_source_sha256,
    validate_qa_images,
)
from backend.app.modules.knowledge.models import (
    KnowledgeImport,
    KnowledgeProduct,
    KnowledgeProductEmbedding,
    KnowledgeSlice,
    KnowledgeSliceEmbedding,
    KnowledgeSource,
    KnowledgeSourceVersion,
    QAEntry,
    QAEntryAsset,
    QAEntryEmbedding,
    StyleEntry,
    StyleEntryEmbedding,
)
from backend.app.modules.knowledge.service import (
    VISIBILITY_RANK,
    KnowledgeConflictError,
    KnowledgeImportResult,
    KnowledgeNotFoundError,
    KnowledgeValidationError,
    _ensure_published_import_index_jobs,
    create_knowledge_source,
    get_source_version,
)
from backend.app.modules.knowledge.v2_markdown import (
    CONTENT_TYPES,
    DOMAINS,
    SCHEMA_VERSION,
    ParsedKnowledgeV2,
    export_v2_markdown,
    parse_v2_markdown,
)


def create_v2_source(
    db: Session, *, title: str, raw_content: str, created_by: str = "admin"
) -> KnowledgeSource:
    return create_knowledge_source(
        db,
        title=title,
        source_type="material",
        visibility="internal",
        raw_content=raw_content,
        created_by=created_by,
        schema_version=SCHEMA_VERSION,
    )


def export_v2_source(db: Session, source_id: str, version_number: int | None = None) -> str:
    source = db.get(KnowledgeSource, source_id)
    if not source or source.schema_version != SCHEMA_VERSION:
        raise KnowledgeNotFoundError("V2 素材不存在")
    return export_v2_markdown(get_source_version(db, source_id, version_number))


def list_v2_ready_sources(db: Session) -> list[KnowledgeSource]:
    return list(
        db.scalars(
            select(KnowledgeSource)
            .where(
                KnowledgeSource.schema_version == SCHEMA_VERSION,
                KnowledgeSource.processing_status == "ready_for_agent",
                KnowledgeSource.status != "archived",
            )
            .order_by(KnowledgeSource.updated_at, KnowledgeSource.id)
        )
    )


def _parsed_hash(parsed: ParsedKnowledgeV2) -> str:
    return compute_source_sha256(
        source_id=parsed.source_id,
        source_version=parsed.source_version,
        title=parsed.title,
        source_type="material",
        visibility=parsed.visibility,
        course_ids=parsed.course_ids,
        raw_content=parsed.raw_content,
        confirmed_facts=parsed.confirmed_facts,
        pending_confirmation_points=parsed.pending_confirmation_points,
        cleaning_requirements=parsed.cleaning_requirements,
        prohibited_content=parsed.prohibited_content,
        source_authorization=parsed.source_authorization,
    )


def _scope(db: Session, item: Any, version: KnowledgeSourceVersion) -> tuple[str, list[str]]:
    visibility = item.visibility or version.visibility
    if VISIBILITY_RANK[visibility] < VISIBILITY_RANK[version.visibility]:
        raise KnowledgeValidationError(f"{item.local_id} 扩大了原素材权限")
    course_ids = list(item.course_ids) if item.course_ids is not None else []
    if visibility == "course":
        if item.course_ids is None:
            course_ids = list(version.course_ids or [])
        if not course_ids:
            raise KnowledgeValidationError(f"{item.local_id} 缺少课程范围")
        if version.visibility == "course" and not set(course_ids).issubset(
            version.course_ids or []
        ):
            raise KnowledgeValidationError(f"{item.local_id} 扩大了原素材课程范围")
    elif course_ids:
        raise KnowledgeValidationError(f"{item.local_id} 的可见范围不能绑定课程")
    if course_ids:
        existing = set(db.scalars(select(Course.id).where(Course.id.in_(course_ids))).all())
        missing = sorted(set(course_ids) - existing)
        if missing:
            raise KnowledgeValidationError(
                f"{item.local_id} 引用了不存在的课程: {'、'.join(missing)}"
            )
    return visibility, list(dict.fromkeys(course_ids))


def _validate_evidence(local_id: str, evidence: str, version: KnowledgeSourceVersion) -> None:
    corpus = "\n".join(
        (
            version.raw_content,
            version.confirmed_facts,
            version.pending_confirmation_points,
            version.cleaning_requirements,
            version.prohibited_content,
            version.source_authorization,
        )
    )
    if evidence not in corpus:
        raise KnowledgeValidationError(f"{local_id} 的来源证据无法在输入版本中定位")


def _import_key(parsed: ParsedKnowledgeV2) -> str:
    raw = f"{parsed.source_id}:{parsed.source_version}:{parsed.source_sha256}:{SCHEMA_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()


def import_v2_markdown(
    db: Session,
    *,
    markdown: str,
    imported_by: str = "agent",
    require_ready_for_agent: bool = False,
    expected_source_id: str | None = None,
) -> KnowledgeImportResult:
    try:
        parsed = parse_v2_markdown(markdown)
    except KnowledgeMarkdownError as exc:
        raise KnowledgeValidationError(str(exc)) from exc
    if expected_source_id is not None and parsed.source_id != expected_source_id:
        raise KnowledgeConflictError("MD 不属于当前素材")
    source = db.get(KnowledgeSource, parsed.source_id)
    if not source or source.schema_version != SCHEMA_VERSION:
        raise KnowledgeNotFoundError("V2 素材不存在")
    version = db.scalar(
        select(KnowledgeSourceVersion).where(
            KnowledgeSourceVersion.source_id == source.id,
            KnowledgeSourceVersion.version_number == parsed.source_version,
            KnowledgeSourceVersion.schema_version == SCHEMA_VERSION,
        )
    )
    if not version:
        raise KnowledgeNotFoundError("V2 素材版本不存在")
    if source.current_version_number != parsed.source_version:
        raise KnowledgeConflictError("只能导入当前版本")
    if require_ready_for_agent and source.processing_status != "ready_for_agent":
        raise KnowledgeConflictError("素材尚未标记为待 Agent 清洗")
    if (
        parsed.source_sha256 != version.source_sha256
        or _parsed_hash(parsed) != version.source_sha256
    ):
        raise KnowledgeConflictError("素材内容或 SHA 与服务器版本不一致")
    if (
        parsed.title != version.title
        or parsed.visibility != version.visibility
        or set(parsed.course_ids) != set(version.course_ids or [])
    ):
        raise KnowledgeConflictError("Markdown 元数据与服务器版本不一致")
    key = _import_key(parsed)
    duplicate = db.scalar(
        select(KnowledgeImport)
        .where(KnowledgeImport.idempotency_key == key)
        .options(
            selectinload(KnowledgeImport.slices),
            selectinload(KnowledgeImport.products),
            selectinload(KnowledgeImport.style_entries),
        )
    )
    if duplicate:
        return KnowledgeImportResult(duplicate, False)

    local_slice_ids = {item.local_id for item in parsed.slices}
    validated_slices: dict[str, tuple[str, list[str]]] = {}
    validated_products: dict[str, tuple[str, list[str]]] = {}
    for item in parsed.slices:
        _validate_evidence(item.local_id, item.source_evidence, version)
        _validate_evidence(item.local_id, item.original_excerpt, version)
        if len(item.structured_content) > 1000:
            raise KnowledgeValidationError(f"{item.local_id} 的结构化内容超过 1000 字")
        validated_slices[item.local_id] = _scope(db, item, version)
    for item in parsed.products:
        _validate_evidence(item.local_id, item.source_evidence, version)
        unknown_slices = set(item.source_slice_ids) - local_slice_ids
        if unknown_slices:
            raise KnowledgeValidationError(
                f"{item.local_id} 引用了不存在的切片: {'、'.join(sorted(unknown_slices))}"
            )
        if len(json.dumps(item.payload, ensure_ascii=False)) > 4000:
            raise KnowledgeValidationError(f"{item.local_id} 的产品卡内容超过 4000 字")
        validated_products[item.local_id] = _scope(db, item, version)
    for item in parsed.style_entries:
        _validate_evidence(item.local_id, item.source_evidence, version)
        if len(item.content) > 1000:
            raise KnowledgeValidationError(f"{item.local_id} 的风格内容超过 1000 字")
    knowledge_import = KnowledgeImport(
        source_id=source.id,
        source_version_id=version.id,
        source_version_number=version.version_number,
        schema_version=SCHEMA_VERSION,
        processor=parsed.processor,
        processor_version=parsed.processor_version,
        source_sha256=parsed.source_sha256,
        idempotency_key=key,
        raw_markdown=markdown,
        cleaned_payload={
            "slice_count": len(parsed.slices),
            "product_count": len(parsed.products),
            "style_count": len(parsed.style_entries),
        },
        status="draft",
        imported_by=imported_by,
    )
    db.add(knowledge_import)
    db.flush()
    for item in parsed.slices:
        visibility, course_ids = validated_slices[item.local_id]
        db.add(
            KnowledgeSlice(
                source_id=source.id,
                source_version_id=version.id,
                import_id=knowledge_import.id,
                local_id=item.local_id,
                dimension=item.dimension,
                title=item.title,
                summary=item.summary,
                original_excerpt=item.original_excerpt,
                structured_content=item.structured_content,
                usage_context=item.usage_context,
                golden_sentence=item.golden_sentence,
                content_type=item.content_type,
                primary_domain=item.primary_domain,
                secondary_domains=list(item.secondary_domains),
                topic_tags=list(item.topic_tags),
                industry_tags=list(item.industry_tags),
                audience_tags=list(item.audience_tags),
                source_evidence=item.source_evidence,
                confirmation=item.confirmation,
                visibility=visibility,
                course_ids=course_ids,
                status="draft",
            )
        )
    for item in parsed.products:
        visibility, course_ids = validated_products[item.local_id]
        db.add(
            KnowledgeProduct(
                source_id=source.id,
                source_version_id=version.id,
                import_id=knowledge_import.id,
                local_id=item.local_id,
                product_type=item.product_type,
                title=item.title,
                summary=item.summary,
                payload=item.payload,
                source_slice_ids=list(item.source_slice_ids),
                primary_domain=item.primary_domain,
                secondary_domains=list(item.secondary_domains),
                source_evidence=item.source_evidence,
                confirmation=item.confirmation,
                visibility=visibility,
                course_ids=course_ids,
                status="draft",
            )
        )
    for item in parsed.style_entries:
        db.add(
            StyleEntry(
                source_id=source.id,
                source_version_id=version.id,
                import_id=knowledge_import.id,
                local_id=item.local_id,
                entry_type=item.entry_type,
                title=item.title,
                content=item.content,
                channels=list(item.channels),
                audiences=list(item.audiences),
                purposes=list(item.purposes),
                source_evidence=item.source_evidence,
                confirmation=item.confirmation,
                visibility=version.visibility,
                course_ids=list(version.course_ids or []),
                status="draft",
            )
        )
    source.processing_status = "imported"
    db.commit()
    db.refresh(knowledge_import)
    return KnowledgeImportResult(knowledge_import, True)


def review_v2_item(
    db: Session,
    *,
    item_kind: str,
    item_id: str,
    decision: str,
    review_note: str = "",
    title: str | None = None,
    content: str | None = None,
    source_evidence: str | None = None,
    confirmation: str | None = None,
    primary_domain: str | None = None,
    secondary_domains: Sequence[str] | None = None,
    content_type: str | None = None,
) -> Any:
    model = {"slice": KnowledgeSlice, "product": KnowledgeProduct, "style": StyleEntry}.get(
        item_kind
    )
    if model is None or decision not in {"approved", "rejected"}:
        raise KnowledgeValidationError("审核类型或决定不合法")
    item = db.get(model, item_id)
    if not item:
        raise KnowledgeNotFoundError("待审核条目不存在")
    if item.knowledge_import.status == "published":
        raise KnowledgeConflictError("已发布导入不可修改")
    version = db.get(KnowledgeSourceVersion, item.source_version_id)
    if not version:
        raise KnowledgeNotFoundError("来源版本不存在")
    next_confirmation = confirmation or item.confirmation
    next_evidence = source_evidence.strip() if source_evidence is not None else item.source_evidence
    _validate_evidence(item.local_id, next_evidence, version)
    if next_confirmation not in {"confirmed", "needs_confirmation"}:
        raise KnowledgeValidationError("确认状态不合法")
    if decision == "approved" and next_confirmation != "confirmed":
        raise KnowledgeConflictError("待确认条目不能通过审核")
    if title is not None:
        if not title.strip():
            raise KnowledgeValidationError("标题不能为空")
        item.title = title.strip()
    if content is not None:
        if not content.strip():
            raise KnowledgeValidationError("内容不能为空")
        if item_kind == "slice":
            item.structured_content = content.strip()
        elif item_kind == "style":
            item.content = content.strip()
        else:
            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise KnowledgeValidationError("产品卡内容必须是 JSON 对象") from exc
            if not isinstance(payload, dict):
                raise KnowledgeValidationError("产品卡内容必须是 JSON 对象")
            item.payload = payload
    if item_kind in {"slice", "product"}:
        next_primary = primary_domain or item.primary_domain
        next_secondary = (
            list(secondary_domains)
            if secondary_domains is not None
            else list(item.secondary_domains or [])
        )
        if next_primary not in DOMAINS or any(value not in DOMAINS for value in next_secondary):
            raise KnowledgeValidationError("业务领域不合法")
        if next_primary in next_secondary or len(next_secondary) > 2:
            raise KnowledgeValidationError("次领域最多两个且不能与主要领域重复")
        item.primary_domain = next_primary
        item.secondary_domains = list(dict.fromkeys(next_secondary))
    if item_kind == "slice" and content_type is not None:
        if content_type not in CONTENT_TYPES:
            raise KnowledgeValidationError("内容形态不合法")
        item.content_type = content_type
    item.source_evidence = next_evidence
    item.confirmation = next_confirmation
    item.status = decision
    item.review_note = review_note.strip()
    db.commit()
    db.refresh(item)
    return item


def publish_v2_import(db: Session, *, import_id: str) -> KnowledgeImport:
    item = db.scalar(
        select(KnowledgeImport)
        .where(KnowledgeImport.id == import_id, KnowledgeImport.schema_version == SCHEMA_VERSION)
        .options(
            selectinload(KnowledgeImport.slices),
            selectinload(KnowledgeImport.products),
            selectinload(KnowledgeImport.style_entries),
        )
    )
    if not item:
        raise KnowledgeNotFoundError("V2 导入记录不存在")
    all_items = [*item.slices, *item.products, *item.style_entries]
    if not all_items or any(
        entry.status != "approved" or entry.confirmation != "confirmed" for entry in all_items
    ):
        raise KnowledgeConflictError("所有条目确认并审核通过后才能原子发布")
    now = datetime.now(UTC)
    for model in (KnowledgeSlice, KnowledgeProduct, StyleEntry):
        previous_items = db.scalars(
            select(model).where(
                model.source_id == item.source_id,
                model.status == "published",
                model.import_id != item.id,
            )
        )
        for previous in previous_items:
            previous.status = "archived"
    previous_imports = db.scalars(
        select(KnowledgeImport).where(
            KnowledgeImport.source_id == item.source_id,
            KnowledgeImport.schema_version == SCHEMA_VERSION,
            KnowledgeImport.status == "published",
            KnowledgeImport.id != item.id,
        )
    )
    for previous in previous_imports:
        previous.status = "archived"
    previous_versions = db.scalars(
        select(KnowledgeSourceVersion).where(
            KnowledgeSourceVersion.source_id == item.source_id,
            KnowledgeSourceVersion.schema_version == SCHEMA_VERSION,
            KnowledgeSourceVersion.status == "published",
            KnowledgeSourceVersion.id != item.source_version_id,
        )
    )
    for previous in previous_versions:
        previous.status = "archived"
    for entry in all_items:
        entry.status = "published"
        entry.published_at = now
    item.status = "published"
    item.published_at = now
    item.source.status = "published"
    item.source.published_version_number = item.source_version_number
    item.source_version.status = "published"
    _ensure_published_import_index_jobs(db, knowledge_import=item)
    db.commit()
    db.refresh(item)
    return item


def _asset_hash(url: str, alt_text: str, storage_key: str) -> str:
    return hashlib.sha256(f"{url}\n{alt_text}\n{storage_key}".encode()).hexdigest()


def create_qa_entry(
    db: Session,
    *,
    question: str,
    answer: str,
    aliases: Sequence[str] = (),
    keywords: Sequence[str] = (),
    visibility: str = "internal",
    course_ids: Sequence[str] = (),
    images: Sequence[dict[str, str]] = (),
    created_by: str = "admin",
) -> QAEntry:
    if not question.strip() or not answer.strip() or len(question.strip()) > 500:
        raise KnowledgeValidationError("标准问和固定答案不能为空，标准问最多 500 字")
    if visibility not in VISIBILITY_RANK:
        raise KnowledgeValidationError("QA 可见范围不合法")
    normalized_courses = list(dict.fromkeys(item.strip() for item in course_ids if item.strip()))
    if (visibility == "course") != bool(normalized_courses):
        raise KnowledgeValidationError("课程可见范围和课程选择不匹配")
    try:
        parsed_images = validate_qa_images(list(images))
    except KnowledgeMarkdownError as exc:
        raise KnowledgeValidationError(str(exc)) from exc
    entry = QAEntry(
        standard_question=question.strip(),
        fixed_answer=answer.strip(),
        aliases=list(dict.fromkeys(item.strip() for item in aliases if item.strip())),
        keywords=list(dict.fromkeys(item.strip() for item in keywords if item.strip())),
        visibility=visibility,
        course_ids=normalized_courses,
        status="draft",
        created_by=created_by,
    )
    db.add(entry)
    db.flush()
    for order, image in enumerate(parsed_images):
        db.add(
            QAEntryAsset(
                qa_entry_id=entry.id,
                public_url=image.public_url,
                storage_key=image.storage_key,
                alt_text=image.alt_text,
                sort_order=order,
                asset_hash=_asset_hash(image.public_url, image.alt_text, image.storage_key),
                status="draft",
            )
        )
    db.commit()
    db.refresh(entry)
    return entry


def set_qa_status(db: Session, *, entry_id: str, status: str, review_note: str = "") -> QAEntry:
    if status not in {"approved", "published", "rejected", "archived"}:
        raise KnowledgeValidationError("QA 状态不合法")
    entry = db.get(QAEntry, entry_id)
    if not entry:
        raise KnowledgeNotFoundError("QA 不存在")
    if status == "published" and entry.status != "approved":
        raise KnowledgeConflictError("QA 需先审核通过")
    entry.status = status
    entry.review_note = review_note.strip()
    if status == "published":
        entry.published_at = datetime.now(UTC)
        for asset in entry.assets:
            asset.status = "published"
    db.commit()
    db.refresh(entry)
    return entry


def store_v2_embedding(
    db: Session,
    *,
    kind: str,
    owner_id: str,
    embedding: Sequence[float],
    model_name: str,
    model_version: str,
    activate: bool = True,
) -> Any:
    mapping = {
        "slice": (KnowledgeSliceEmbedding, "slice_id"),
        "product": (KnowledgeProductEmbedding, "product_id"),
        "style": (StyleEntryEmbedding, "style_entry_id"),
        "qa": (QAEntryEmbedding, "qa_entry_id"),
    }
    if kind not in mapping or len(embedding) != 1024:
        raise KnowledgeValidationError("V2 Embedding 类型或维度不合法")
    if not all(isinstance(value, (int, float)) for value in embedding):
        raise KnowledgeValidationError("V2 Embedding 必须是数值数组")
    model, owner_column = mapping[kind]
    content_hash = hashlib.sha256(
        json.dumps([round(float(value), 8) for value in embedding], separators=(",", ":")).encode()
    ).hexdigest()
    if activate:
        db.query(model).filter(getattr(model, owner_column) == owner_id).update(
            {"is_active": False}
        )
    stored = model(
        **{owner_column: owner_id},
        model_name=model_name,
        model_version=model_version,
        dimension=1024,
        content_hash=content_hash,
        embedding=[float(value) for value in embedding],
        is_active=activate,
    )
    db.add(stored)
    db.flush()
    return stored


__all__ = [
    "create_v2_source",
    "export_v2_source",
    "list_v2_ready_sources",
    "import_v2_markdown",
    "review_v2_item",
    "publish_v2_import",
    "create_qa_entry",
    "set_qa_status",
    "store_v2_embedding",
]
