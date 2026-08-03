from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = "avatar-knowledge/v1"
CLEANED_SCHEMA_VERSION = 1

SOURCE_TYPES = {
    "material",
    "transcript",
    "article",
    "faq",
    "notes",
    "course_material",
    "interview",
    "pure_qa",
    "other",
}
VISIBILITIES = {"public", "course", "internal"}
UNIT_TYPES = {
    "identity_fact",
    "fact",
    "judgement",
    "method",
    "faq",
    "qa",
    "style_rule",
    "style_sample",
    "prohibition",
    "answer_boundary",
}
CONFIRMATION_STATES = {"confirmed", "needs_confirmation"}

SECTION_KEYS = {
    "原始语料": "raw_content",
    "已确认事实": "confirmed_facts",
    "待确认信息点": "pending_confirmation_points",
    "清洗与调整要求": "cleaning_requirements",
    "禁止项与引用边界": "prohibited_content",
    "来源及授权说明": "source_authorization",
    "清洗结果": "cleaned_result",
}
REQUIRED_SECTIONS = tuple(SECTION_KEYS)
UNIT_FIELDS = {
    "local_id",
    "type",
    "title",
    "content",
    "aliases",
    "keywords",
    "channels",
    "source_evidence",
    "confirmation",
    "visibility",
    "course_ids",
    "question",
    "answer",
    "images",
}

MAX_QA_IMAGES = 6


class KnowledgeMarkdownError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedKnowledgeImage:
    public_url: str
    alt_text: str
    storage_key: str = ""


@dataclass(frozen=True)
class ParsedKnowledgeUnit:
    local_id: str
    unit_type: str
    title: str
    content: str
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]
    channels: tuple[str, ...]
    source_evidence: str
    confirmation: str
    visibility: str | None
    course_ids: tuple[str, ...] | None
    standard_question: str
    standard_answer: str
    images: tuple[ParsedKnowledgeImage, ...]


@dataclass(frozen=True)
class ParsedKnowledgeMarkdown:
    schema_version: str
    source_id: str
    source_version: int
    source_sha256: str
    title: str
    source_type: str
    visibility: str
    course_ids: tuple[str, ...]
    raw_content: str
    confirmed_facts: str
    pending_confirmation_points: str
    cleaning_requirements: str
    prohibited_content: str
    source_authorization: str
    cleaned_schema: int
    processor: str
    processor_version: str
    units: tuple[ParsedKnowledgeUnit, ...]


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def compute_source_sha256(
    *,
    source_id: str,
    source_version: int,
    title: str,
    source_type: str,
    visibility: str,
    course_ids: list[str] | tuple[str, ...],
    raw_content: str,
    confirmed_facts: str,
    pending_confirmation_points: str,
    cleaning_requirements: str,
    prohibited_content: str,
    source_authorization: str,
) -> str:
    payload = {
        "source_id": source_id,
        "source_version": int(source_version),
        "title": _normalize_text(title),
        "source_type": source_type.strip(),
        "visibility": visibility.strip(),
        "course_ids": sorted({item.strip() for item in course_ids if item.strip()}),
        "raw_content": _normalize_text(raw_content),
        "confirmed_facts": _normalize_text(confirmed_facts),
        "pending_confirmation_points": _normalize_text(pending_confirmation_points),
        "cleaning_requirements": _normalize_text(cleaning_requirements),
        "prohibited_content": _normalize_text(prohibited_content),
        "source_authorization": _normalize_text(source_authorization),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _yaml_scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def export_knowledge_markdown(version: Any) -> str:
    front_matter = [
        "---",
        f"schema: {_yaml_scalar(SCHEMA_VERSION)}",
        f"source_id: {_yaml_scalar(version.source_id)}",
        f"source_version: {version.version_number}",
        f"source_sha256: {_yaml_scalar(version.source_sha256)}",
        f"title: {_yaml_scalar(version.title)}",
        f"source_type: {_yaml_scalar(version.source_type)}",
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
    for heading, content in sections:
        body.extend([f"# {heading}", "", _normalize_text(content), ""])
    body.extend(
        [
            "# 清洗结果",
            "",
            "```yaml",
            f"cleaned_schema: {CLEANED_SCHEMA_VERSION}",
            'processor: "manual"',
            'processor_version: "unspecified"',
            "units: []",
            "```",
            "",
        ]
    )
    return "\n".join([*front_matter, "", *body])


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {'"', "'", "[", "{"}:
        try:
            if value[0] == "'" and value[-1:] == "'":
                return value[1:-1].replace("''", "'")
            return json.loads(value)
        except (json.JSONDecodeError, IndexError) as exc:
            raise KnowledgeMarkdownError(f"无法解析字段值: {value[:80]}") from exc
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _parse_flat_mapping(raw: str, *, label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1].isspace() or ":" not in line:
            raise KnowledgeMarkdownError(f"{label} 仅允许顶层 key: value")
        key, value = line.split(":", 1)
        normalized_key = key.strip()
        if normalized_key in result:
            raise KnowledgeMarkdownError(f"{label} 存在重复字段: {normalized_key}")
        result[normalized_key] = _parse_scalar(value)
    return result


def _split_document(markdown: str) -> tuple[dict[str, Any], dict[str, str]]:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        raise KnowledgeMarkdownError("Markdown 必须以 YAML front matter 开始")
    end = normalized.find("\n---\n", 4)
    if end < 0:
        raise KnowledgeMarkdownError("YAML front matter 未闭合")
    front_matter = _parse_flat_mapping(normalized[4:end], label="front matter")
    body = normalized[end + 5 :]
    heading_pattern = re.compile(
        r"(?m)^# (" + "|".join(re.escape(item) for item in REQUIRED_SECTIONS) + r")\s*$"
    )
    matches = list(heading_pattern.finditer(body))
    found_names = [match.group(1) for match in matches]
    missing = [name for name in REQUIRED_SECTIONS if name not in found_names]
    if missing:
        raise KnowledgeMarkdownError("缺少章节: " + "、".join(missing))
    if len(found_names) != len(set(found_names)):
        raise KnowledgeMarkdownError("标准章节不得重复")
    positions = {match.group(1): (match.end(), index) for index, match in enumerate(matches)}
    sections: dict[str, str] = {}
    for name in REQUIRED_SECTIONS:
        start, index = positions[name]
        end_pos = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[SECTION_KEYS[name]] = _normalize_text(body[start:end_pos])
    return front_matter, sections


def _cleaned_yaml_body(raw: str) -> str:
    match = re.fullmatch(r"```(?:yaml|yml)?\s*\n([\s\S]*?)\n```\s*", raw.strip())
    if not match:
        raise KnowledgeMarkdownError("清洗结果必须放在单个 ```yaml 代码块内")
    return match.group(1)


def _parse_unit_mapping(lines: list[str], start: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    index = start
    while index < len(lines):
        line = lines[index]
        if re.match(r"^  -\s+", line) and index != start:
            break
        if index == start:
            content = re.sub(r"^  -\s+", "", line, count=1)
        else:
            if not line.strip():
                index += 1
                continue
            if not line.startswith("    "):
                break
            content = line[4:]
        if ":" not in content:
            raise KnowledgeMarkdownError("清洗单元字段必须使用 key: value")
        key, raw_value = content.split(":", 1)
        key = key.strip()
        if key not in UNIT_FIELDS:
            raise KnowledgeMarkdownError(f"清洗单元包含未知字段: {key}")
        if key in result:
            raise KnowledgeMarkdownError(f"清洗单元存在重复字段: {key}")
        if raw_value.strip() in {"|", ">"}:
            block_lines: list[str] = []
            index += 1
            while index < len(lines):
                block_line = lines[index]
                if block_line.startswith("      "):
                    block_lines.append(block_line[6:])
                    index += 1
                    continue
                if not block_line.strip():
                    block_lines.append("")
                    index += 1
                    continue
                break
            result[key] = _normalize_text("\n".join(block_lines))
            continue
        result[key] = _parse_scalar(raw_value)
        index += 1
    return result, index


def _string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise KnowledgeMarkdownError(f"{field} 必须是字符串数组")
    return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeMarkdownError(f"清洗单元缺少字段: {key}")
    return value.strip()


def _validate_https_image_url(value: Any) -> str:
    if not isinstance(value, str):
        raise KnowledgeMarkdownError("图片 URL 必须是字符串")
    url = value.strip()
    if not url or len(url) > 2000:
        raise KnowledgeMarkdownError("图片 URL 不能为空且不得超过 2000 字符")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise KnowledgeMarkdownError("图片 URL 格式不合法") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise KnowledgeMarkdownError("图片 URL 必须使用 https")
    if parsed.username or parsed.password or parsed.fragment:
        raise KnowledgeMarkdownError("图片 URL 不允许账号信息或 fragment")
    if port not in {None, 443}:
        raise KnowledgeMarkdownError("图片 URL 只允许标准 HTTPS 端口")
    hostname = parsed.hostname.rstrip(".").lower()
    if (
        hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal", ".lan"))
        or "." not in hostname
        or bool(re.fullmatch(r"[0-9.]+", hostname))
        or not re.fullmatch(r"[a-z0-9.-]+", hostname)
    ):
        raise KnowledgeMarkdownError("图片 URL 必须指向公网域名")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip and not ip.is_global:
        raise KnowledgeMarkdownError("图片 URL 不允许私网或保留地址")
    return url


def validate_qa_images(value: Any) -> tuple[ParsedKnowledgeImage, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise KnowledgeMarkdownError("images 必须是数组")
    if len(value) > MAX_QA_IMAGES:
        raise KnowledgeMarkdownError(f"每条 QA 最多 {MAX_QA_IMAGES} 张图片")
    images: list[ParsedKnowledgeImage] = []
    seen_urls: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise KnowledgeMarkdownError(f"第 {index} 张图片必须是对象")
        unknown = set(item) - {"url", "alt_text", "storage_key"}
        if unknown:
            raise KnowledgeMarkdownError(
                f"第 {index} 张图片包含未知字段: {'、'.join(sorted(unknown))}"
            )
        url = _validate_https_image_url(item.get("url"))
        if url in seen_urls:
            raise KnowledgeMarkdownError("同一 QA 不允许重复图片 URL")
        seen_urls.add(url)
        alt_text = item.get("alt_text", "")
        if not isinstance(alt_text, str) or len(alt_text.strip()) > 200:
            raise KnowledgeMarkdownError("图片 alt_text 必须是 200 字以内的纯文本")
        alt_text = alt_text.strip()
        if "<" in alt_text or ">" in alt_text or any(ord(char) < 32 for char in alt_text):
            raise KnowledgeMarkdownError("图片 alt_text 不允许 HTML 或控制字符")
        storage_key = item.get("storage_key", "")
        if not isinstance(storage_key, str) or len(storage_key.strip()) > 500:
            raise KnowledgeMarkdownError("图片 storage_key 不合法")
        storage_key = storage_key.strip()
        if storage_key and not re.fullmatch(r"[A-Za-z0-9._/-]+", storage_key):
            raise KnowledgeMarkdownError("图片 storage_key 只能包含安全路径字符")
        images.append(
            ParsedKnowledgeImage(
                public_url=url,
                alt_text=alt_text,
                storage_key=storage_key,
            )
        )
    return tuple(images)


def _safe_processor_field(value: Any, *, field: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise KnowledgeMarkdownError(f"{field} 长度或类型不合法")
    normalized = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", normalized):
        raise KnowledgeMarkdownError(f"{field} 只能包含安全标识符字符")
    return normalized


def _parse_cleaned_result(
    raw: str,
) -> tuple[int, str, str, tuple[ParsedKnowledgeUnit, ...]]:
    lines = _cleaned_yaml_body(raw).splitlines()
    cleaned_schema: int | None = None
    processor = "manual"
    processor_version = "unspecified"
    seen_processor = False
    seen_processor_version = False
    units: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line.startswith("processor:"):
            if seen_processor:
                raise KnowledgeMarkdownError("processor 不得重复")
            processor = _safe_processor_field(
                _parse_scalar(line.split(":", 1)[1]),
                field="processor",
                max_length=80,
            )
            seen_processor = True
            index += 1
            continue
        if line.startswith("processor_version:"):
            if seen_processor_version:
                raise KnowledgeMarkdownError("processor_version 不得重复")
            processor_version = _safe_processor_field(
                _parse_scalar(line.split(":", 1)[1]),
                field="processor_version",
                max_length=120,
            )
            seen_processor_version = True
            index += 1
            continue
        if line.startswith("cleaned_schema:"):
            if cleaned_schema is not None:
                raise KnowledgeMarkdownError("cleaned_schema 不得重复")
            cleaned_schema = _parse_scalar(line.split(":", 1)[1])
            index += 1
            continue
        if line.strip() == "units: []":
            index += 1
            continue
        if line.strip() == "units:":
            index += 1
            while index < len(lines):
                if not lines[index].strip():
                    index += 1
                    continue
                if not re.match(r"^  -\s+", lines[index]):
                    raise KnowledgeMarkdownError("units 必须是两空格缩进的列表")
                unit, index = _parse_unit_mapping(lines, index)
                units.append(unit)
            continue
        raise KnowledgeMarkdownError(f"清洗结果包含未知顶层字段: {line.strip()[:80]}")
    if cleaned_schema != CLEANED_SCHEMA_VERSION:
        raise KnowledgeMarkdownError(
            f"cleaned_schema 必须为 {CLEANED_SCHEMA_VERSION}"
        )

    parsed: list[ParsedKnowledgeUnit] = []
    seen_local_ids: set[str] = set()
    for data in units:
        local_id = _required_string(data, "local_id")
        if local_id in seen_local_ids:
            raise KnowledgeMarkdownError(f"local_id 重复: {local_id}")
        seen_local_ids.add(local_id)
        unit_type = _required_string(data, "type")
        confirmation = _required_string(data, "confirmation")
        if unit_type not in UNIT_TYPES:
            raise KnowledgeMarkdownError(f"未知知识类型: {unit_type}")
        if confirmation not in CONFIRMATION_STATES:
            raise KnowledgeMarkdownError(f"未知确认状态: {confirmation}")
        if unit_type == "qa":
            standard_question = _required_string(data, "question")
            standard_answer = _required_string(data, "answer")
            if "title" in data and str(data["title"]).strip() != standard_question:
                raise KnowledgeMarkdownError("QA title 如提供必须与 question 完全一致")
            if "content" in data and str(data["content"]).strip() != standard_answer:
                raise KnowledgeMarkdownError("QA content 如提供必须与 answer 完全一致")
            title = standard_question
            content = standard_answer
            if len(standard_question) > 500:
                raise KnowledgeMarkdownError("QA 标准问不得超过 500 字")
        else:
            if "question" in data or "answer" in data or data.get("images"):
                raise KnowledgeMarkdownError("只有 qa 类型可以包含 question、answer 或 images")
            standard_question = ""
            standard_answer = ""
            title = _required_string(data, "title")
            content = _required_string(data, "content")
        visibility = data.get("visibility")
        if visibility is not None and visibility not in VISIBILITIES:
            raise KnowledgeMarkdownError(f"未知可见范围: {visibility}")
        parsed.append(
            ParsedKnowledgeUnit(
                local_id=local_id,
                unit_type=unit_type,
                title=title,
                content=content,
                aliases=_string_list(data.get("aliases", []), field="aliases"),
                keywords=_string_list(data.get("keywords", []), field="keywords"),
                channels=_string_list(data.get("channels", []), field="channels"),
                source_evidence=_required_string(data, "source_evidence"),
                confirmation=confirmation,
                visibility=visibility,
                course_ids=(
                    _string_list(data["course_ids"], field="course_ids")
                    if "course_ids" in data
                    else None
                ),
                standard_question=standard_question,
                standard_answer=standard_answer,
                images=validate_qa_images(data.get("images", [])),
            )
        )
    if not parsed:
        raise KnowledgeMarkdownError("清洗结果至少需要一个知识单元")
    return cleaned_schema, processor, processor_version, tuple(parsed)


def parse_knowledge_markdown(markdown: str) -> ParsedKnowledgeMarkdown:
    if len(markdown.encode("utf-8")) > 5 * 1024 * 1024:
        raise KnowledgeMarkdownError("Markdown 文件不得超过 5MB")
    front, sections = _split_document(markdown)
    required_front = {
        "schema",
        "source_id",
        "source_version",
        "source_sha256",
        "title",
        "source_type",
        "visibility",
        "course_ids",
    }
    missing = sorted(required_front - set(front))
    unknown = sorted(set(front) - required_front)
    if missing:
        raise KnowledgeMarkdownError("front matter 缺少字段: " + "、".join(missing))
    if unknown:
        raise KnowledgeMarkdownError("front matter 包含未知字段: " + "、".join(unknown))
    if front["schema"] != SCHEMA_VERSION:
        raise KnowledgeMarkdownError(f"schema 必须为 {SCHEMA_VERSION}")
    if not isinstance(front["source_version"], int) or front["source_version"] < 1:
        raise KnowledgeMarkdownError("source_version 必须是正整数")
    if front["source_type"] not in SOURCE_TYPES:
        raise KnowledgeMarkdownError(f"未知来源类型: {front['source_type']}")
    if front["visibility"] not in VISIBILITIES:
        raise KnowledgeMarkdownError(f"未知可见范围: {front['visibility']}")
    course_ids = _string_list(front["course_ids"], field="course_ids")
    cleaned_schema, processor, processor_version, units = _parse_cleaned_result(
        sections["cleaned_result"]
    )
    return ParsedKnowledgeMarkdown(
        schema_version=str(front["schema"]),
        source_id=str(front["source_id"]),
        source_version=front["source_version"],
        source_sha256=str(front["source_sha256"]),
        title=str(front["title"]),
        source_type=str(front["source_type"]),
        visibility=str(front["visibility"]),
        course_ids=course_ids,
        raw_content=sections["raw_content"],
        confirmed_facts=sections["confirmed_facts"],
        pending_confirmation_points=sections["pending_confirmation_points"],
        cleaning_requirements=sections["cleaning_requirements"],
        prohibited_content=sections["prohibited_content"],
        source_authorization=sections["source_authorization"],
        cleaned_schema=cleaned_schema,
        processor=processor,
        processor_version=processor_version,
        units=units,
    )
