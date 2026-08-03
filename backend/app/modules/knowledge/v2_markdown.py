from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.app.modules.knowledge.markdown import (
    KnowledgeMarkdownError,
    _cleaned_yaml_body,
    _normalize_text,
    _parse_scalar,
    _safe_processor_field,
    _split_document,
    _string_list,
    _yaml_scalar,
)

SCHEMA_VERSION = "avatar-knowledge/v2"
CLEANED_SCHEMA_VERSION = 2
DIMENSIONS = ("values", "models", "methods", "concepts", "facts", "quotes")
DIMENSION_LABELS = {
    "values": "底层心法",
    "models": "思维模型",
    "methods": "方法工具",
    "concepts": "知识概念",
    "facts": "案例事实",
    "quotes": "金句语录",
}
CONTENT_TYPES = ("cognition", "methodology", "case")
CONTENT_TYPE_LABELS = {"cognition": "认知判断", "methodology": "方法论", "case": "案例"}
DOMAINS = (
    "personal_ai",
    "org_ai",
    "ai_frontier",
    "biz_ops",
    "opc_growth",
    "heart_power",
    "macro_trend",
)
DOMAIN_LABELS = {
    "personal_ai": "个人 AI 化",
    "org_ai": "企业组织 AI 化",
    "ai_frontier": "AI 前沿",
    "biz_ops": "商业与运营",
    "opc_growth": "一人公司成长",
    "heart_power": "心力提升",
    "macro_trend": "宏观趋势",
}
PRODUCT_TYPES = ("judgement_card", "method_card", "case_card")
STYLE_TYPES = ("rule", "sample")
VISIBILITIES = ("public", "course", "internal")
CONFIRMATIONS = ("confirmed", "needs_confirmation")


@dataclass(frozen=True)
class ParsedSlice:
    local_id: str
    dimension: str
    title: str
    summary: str
    original_excerpt: str
    structured_content: str
    usage_context: str
    golden_sentence: str
    content_type: str
    primary_domain: str
    secondary_domains: tuple[str, ...]
    topic_tags: tuple[str, ...]
    industry_tags: tuple[str, ...]
    audience_tags: tuple[str, ...]
    source_evidence: str
    confirmation: str
    visibility: str | None
    course_ids: tuple[str, ...] | None


@dataclass(frozen=True)
class ParsedProduct:
    local_id: str
    product_type: str
    title: str
    summary: str
    payload: dict[str, Any]
    source_slice_ids: tuple[str, ...]
    primary_domain: str
    secondary_domains: tuple[str, ...]
    source_evidence: str
    confirmation: str
    visibility: str | None
    course_ids: tuple[str, ...] | None


@dataclass(frozen=True)
class ParsedStyleEntry:
    local_id: str
    entry_type: str
    title: str
    content: str
    channels: tuple[str, ...]
    audiences: tuple[str, ...]
    purposes: tuple[str, ...]
    source_evidence: str
    confirmation: str


@dataclass(frozen=True)
class ParsedKnowledgeV2:
    source_id: str
    source_version: int
    source_sha256: str
    title: str
    visibility: str
    course_ids: tuple[str, ...]
    raw_content: str
    confirmed_facts: str
    pending_confirmation_points: str
    cleaning_requirements: str
    prohibited_content: str
    source_authorization: str
    processor: str
    processor_version: str
    slices: tuple[ParsedSlice, ...]
    products: tuple[ParsedProduct, ...]
    style_entries: tuple[ParsedStyleEntry, ...]


def export_v2_markdown(version: Any) -> str:
    front = [
        "---",
        f"schema: {_yaml_scalar(SCHEMA_VERSION)}",
        f"source_id: {_yaml_scalar(version.source_id)}",
        f"source_version: {version.version_number}",
        f"source_sha256: {_yaml_scalar(version.source_sha256)}",
        f"title: {_yaml_scalar(version.title)}",
        f"visibility: {_yaml_scalar(version.visibility)}",
        f"course_ids: {_yaml_scalar(list(version.course_ids or []))}",
        "---",
    ]
    sections = [
        ("原始语料", version.raw_content),
        ("已确认事实", version.confirmed_facts),
        ("待确认信息点", version.pending_confirmation_points),
        ("清洗与调整要求", version.cleaning_requirements),
        ("禁止项与引用边界", version.prohibited_content),
        ("来源及授权说明", version.source_authorization),
    ]
    body: list[str] = []
    for title, value in sections:
        body.extend([f"# {title}", "", _normalize_text(value or ""), ""])
    body.extend(
        [
            "# 清洗结果",
            "",
            "```yaml",
            "cleaned_schema: 2",
            'processor: "manual"',
            'processor_version: "unspecified"',
            "slices: []",
            "products: []",
            "style_entries: []",
            "```",
            "",
        ]
    )
    return "\n".join([*front, "", *body])


def _required(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeMarkdownError(f"条目缺少字段: {key}")
    return value.strip()


def _parse_item(lines: list[str], start: int, allowed: set[str]) -> tuple[dict[str, Any], int]:
    data: dict[str, Any] = {}
    index = start
    while index < len(lines):
        line = lines[index]
        if index != start and re.match(r"^  -\s+", line):
            break
        content = (
            re.sub(r"^  -\s+", "", line, count=1)
            if index == start
            else line[4:]
            if line.startswith("    ")
            else ""
        )
        if not content:
            if line.strip() and index != start:
                break
            index += 1
            continue
        if ":" not in content:
            raise KnowledgeMarkdownError("清洗条目字段必须使用 key: value")
        key, raw_value = content.split(":", 1)
        key = key.strip()
        if key not in allowed:
            raise KnowledgeMarkdownError(f"清洗条目包含未知字段: {key}")
        if key in data:
            raise KnowledgeMarkdownError(f"清洗条目字段重复: {key}")
        if raw_value.strip() in {"|", ">"}:
            block: list[str] = []
            index += 1
            while index < len(lines) and (
                lines[index].startswith("      ") or not lines[index].strip()
            ):
                block.append(lines[index][6:] if lines[index].startswith("      ") else "")
                index += 1
            data[key] = _normalize_text("\n".join(block))
            continue
        data[key] = _parse_scalar(raw_value)
        index += 1
    return data, index


def _parse_cleaned(
    raw: str,
) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    lines = _cleaned_yaml_body(raw).splitlines()
    scalar_fields: dict[str, Any] = {}
    groups: dict[str, list[dict[str, Any]]] = {"slices": [], "products": [], "style_entries": []}
    allowed = {
        "slices": {
            "local_id",
            "dimension",
            "title",
            "summary",
            "original_excerpt",
            "structured_content",
            "usage_context",
            "golden_sentence",
            "content_type",
            "primary_domain",
            "secondary_domains",
            "topic_tags",
            "industry_tags",
            "audience_tags",
            "source_evidence",
            "confirmation",
            "visibility",
            "course_ids",
        },
        "products": {
            "local_id",
            "type",
            "title",
            "summary",
            "payload",
            "source_slice_ids",
            "primary_domain",
            "secondary_domains",
            "source_evidence",
            "confirmation",
            "visibility",
            "course_ids",
        },
        "style_entries": {
            "local_id",
            "type",
            "title",
            "content",
            "channels",
            "audiences",
            "purposes",
            "source_evidence",
            "confirmation",
        },
    }
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        matched_group = next(
            (name for name in groups if line.strip() in {f"{name}:", f"{name}: []"}), None
        )
        if matched_group:
            empty = line.strip().endswith("[]")
            index += 1
            if empty:
                continue
            while index < len(lines) and (
                not lines[index].strip() or re.match(r"^  -\s+", lines[index])
            ):
                if not lines[index].strip():
                    index += 1
                    continue
                item, index = _parse_item(lines, index, allowed[matched_group])
                groups[matched_group].append(item)
            continue
        if not line.startswith(" ") and ":" in line:
            key, value = line.split(":", 1)
            if key not in {"cleaned_schema", "processor", "processor_version"}:
                raise KnowledgeMarkdownError(f"清洗结果包含未知顶层字段: {key}")
            if key in scalar_fields:
                raise KnowledgeMarkdownError(f"清洗结果字段重复: {key}")
            scalar_fields[key] = _parse_scalar(value)
            index += 1
            continue
        raise KnowledgeMarkdownError(f"无法解析清洗结果: {line.strip()[:80]}")
    if scalar_fields.get("cleaned_schema") != CLEANED_SCHEMA_VERSION:
        raise KnowledgeMarkdownError("cleaned_schema 必须为 2")
    processor = _safe_processor_field(
        scalar_fields.get("processor", "manual"), field="processor", max_length=80
    )
    processor_version = _safe_processor_field(
        scalar_fields.get("processor_version", "unspecified"),
        field="processor_version",
        max_length=120,
    )
    if not any(groups.values()):
        raise KnowledgeMarkdownError("清洗结果至少需要一条切片、产品卡或风格条目")
    return (
        processor,
        processor_version,
        groups["slices"],
        groups["products"],
        groups["style_entries"],
    )


def _domains(data: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    primary = _required(data, "primary_domain")
    secondary = _string_list(data.get("secondary_domains", []), field="secondary_domains")
    if primary not in DOMAINS or any(item not in DOMAINS for item in secondary):
        raise KnowledgeMarkdownError("业务领域必须使用封闭枚举")
    if primary in secondary or len(secondary) > 2:
        raise KnowledgeMarkdownError("次领域最多两个，且不得与主要领域重复")
    return primary, secondary


def _scope(data: dict[str, Any]) -> tuple[str | None, tuple[str, ...] | None]:
    visibility = data.get("visibility")
    if visibility is not None and visibility not in VISIBILITIES:
        raise KnowledgeMarkdownError("可见范围不合法")
    return visibility, _string_list(
        data["course_ids"], field="course_ids"
    ) if "course_ids" in data else None


def parse_v2_markdown(markdown: str) -> ParsedKnowledgeV2:
    if len(markdown.encode("utf-8")) > 5 * 1024 * 1024:
        raise KnowledgeMarkdownError("Markdown 文件不得超过 5MB")
    front, sections = _split_document(markdown)
    required = {
        "schema",
        "source_id",
        "source_version",
        "source_sha256",
        "title",
        "visibility",
        "course_ids",
    }
    if set(front) != required:
        missing = required - set(front)
        unknown = set(front) - required
        raise KnowledgeMarkdownError(
            f"front matter 字段不匹配，缺少 {sorted(missing)}，多出 {sorted(unknown)}"
        )
    if front["schema"] != SCHEMA_VERSION or front["visibility"] not in VISIBILITIES:
        raise KnowledgeMarkdownError("V2 schema 或可见范围不合法")
    if not isinstance(front["source_version"], int) or front["source_version"] < 1:
        raise KnowledgeMarkdownError("source_version 必须是正整数")
    processor, processor_version, raw_slices, raw_products, raw_styles = _parse_cleaned(
        sections["cleaned_result"]
    )
    seen_ids: set[str] = set()
    slices: list[ParsedSlice] = []
    for data in raw_slices:
        local_id = _required(data, "local_id")
        if local_id in seen_ids:
            raise KnowledgeMarkdownError(f"local_id 重复: {local_id}")
        seen_ids.add(local_id)
        dimension = _required(data, "dimension")
        content_type = _required(data, "content_type")
        confirmation = _required(data, "confirmation")
        if (
            dimension not in DIMENSIONS
            or content_type not in CONTENT_TYPES
            or confirmation not in CONFIRMATIONS
        ):
            raise KnowledgeMarkdownError(f"{local_id} 的维度、内容形态或确认状态不合法")
        primary, secondary = _domains(data)
        visibility, course_ids = _scope(data)
        slices.append(
            ParsedSlice(
                local_id,
                dimension,
                _required(data, "title"),
                str(data.get("summary", "")).strip(),
                _required(data, "original_excerpt"),
                _required(data, "structured_content"),
                str(data.get("usage_context", "")).strip(),
                str(data.get("golden_sentence", "")).strip(),
                content_type,
                primary,
                secondary,
                _string_list(data.get("topic_tags", []), field="topic_tags"),
                _string_list(data.get("industry_tags", []), field="industry_tags"),
                _string_list(data.get("audience_tags", []), field="audience_tags"),
                _required(data, "source_evidence"),
                confirmation,
                visibility,
                course_ids,
            )
        )
    products: list[ParsedProduct] = []
    for data in raw_products:
        local_id = _required(data, "local_id")
        if local_id in seen_ids:
            raise KnowledgeMarkdownError(f"local_id 重复: {local_id}")
        seen_ids.add(local_id)
        product_type = _required(data, "type")
        confirmation = _required(data, "confirmation")
        if product_type not in PRODUCT_TYPES or confirmation not in CONFIRMATIONS:
            raise KnowledgeMarkdownError(f"{local_id} 的产品类型或确认状态不合法")
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            raise KnowledgeMarkdownError("产品卡 payload 必须是 JSON 对象")
        primary, secondary = _domains(data)
        visibility, course_ids = _scope(data)
        products.append(
            ParsedProduct(
                local_id,
                product_type,
                _required(data, "title"),
                str(data.get("summary", "")).strip(),
                payload,
                _string_list(data.get("source_slice_ids", []), field="source_slice_ids"),
                primary,
                secondary,
                _required(data, "source_evidence"),
                confirmation,
                visibility,
                course_ids,
            )
        )
    styles: list[ParsedStyleEntry] = []
    for data in raw_styles:
        local_id = _required(data, "local_id")
        if local_id in seen_ids:
            raise KnowledgeMarkdownError(f"local_id 重复: {local_id}")
        seen_ids.add(local_id)
        entry_type = _required(data, "type")
        confirmation = _required(data, "confirmation")
        if entry_type not in STYLE_TYPES or confirmation not in CONFIRMATIONS:
            raise KnowledgeMarkdownError(f"{local_id} 的风格类型或确认状态不合法")
        styles.append(
            ParsedStyleEntry(
                local_id,
                entry_type,
                _required(data, "title"),
                _required(data, "content"),
                _string_list(data.get("channels", []), field="channels"),
                _string_list(data.get("audiences", []), field="audiences"),
                _string_list(data.get("purposes", []), field="purposes"),
                _required(data, "source_evidence"),
                confirmation,
            )
        )
    return ParsedKnowledgeV2(
        str(front["source_id"]),
        front["source_version"],
        str(front["source_sha256"]),
        str(front["title"]),
        str(front["visibility"]),
        _string_list(front["course_ids"], field="course_ids"),
        sections["raw_content"],
        sections["confirmed_facts"],
        sections["pending_confirmation_points"],
        sections["cleaning_requirements"],
        sections["prohibited_content"],
        sections["source_authorization"],
        processor,
        processor_version,
        tuple(slices),
        tuple(products),
        tuple(styles),
    )


__all__ = [
    "SCHEMA_VERSION",
    "DIMENSIONS",
    "DIMENSION_LABELS",
    "CONTENT_TYPES",
    "CONTENT_TYPE_LABELS",
    "DOMAINS",
    "DOMAIN_LABELS",
    "PRODUCT_TYPES",
    "STYLE_TYPES",
    "ParsedKnowledgeV2",
    "parse_v2_markdown",
    "export_v2_markdown",
]
