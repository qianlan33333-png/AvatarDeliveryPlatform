# AI 分身语料清洗标准 V2

版本：`avatar-knowledge/v2`

本文件是后台、外部清洗 Agent 与运行时知识检索之间的唯一交换契约。系统不自动蒸馏：运营保存原始素材，外部 Agent 清洗，运营逐条审核并原子发布。

## 1. 数据分层

`原始素材 → 六维知识切片 → 精选产品卡 → 问答/话术消费`

- 原始素材只要求标题和完整原文，不做来源分类。
- 六维切片固定为 `values / models / methods / concepts / facts / quotes`。
- 内容形态固定为 `cognition / methodology / case`。
- 精选产品卡固定为 `judgement_card / method_card / case_card`，由外部 Agent 或人工整理，不在系统内自动生成。
- 表达风格固定为 `rule / sample`，只进入话术，不得作为事实。
- 纯 QA 使用独立后台和 `avatar-qa/v1` 逻辑，不进入本契约。

## 2. 生命周期

1. 后台新增素材，只填写标题和原文；默认内部草稿。
2. 高级信息可维护权限、课程范围、确认点、清洗要求、禁止项和授权说明；每次修改生成不可变新版本。
3. 标记待清洗后，Agent 领取并下载 MD。
4. Agent 只能填写“清洗结果”，不得修改 front matter、输入章节或 SHA。
5. 系统确定性校验枚举、权限、证据、来源切片和幂等键。
6. 切片、产品卡和风格条目逐条编辑、通过或驳回。
7. 所有条目均已确认且审核通过后一次性发布；待确认内容不得发布。

## 3. 完整 Markdown 模板

````md
---
schema: "avatar-knowledge/v2"
source_id: "后台生成 UUID"
source_version: 1
source_sha256: "后台生成 SHA256"
title: "素材标题"
visibility: "internal"
course_ids: []
---

# 原始语料

后台导出的原文，不得修改。

# 已确认事实

# 待确认信息点

# 清洗与调整要求

# 禁止项与引用边界

# 来源及授权说明

# 清洗结果

```yaml
cleaned_schema: 2
processor: "avatar-cleaner"
processor_version: "2.0.0"
slices:
  - local_id: "KS-001"
    dimension: "values"
    title: "先解决真实问题"
    summary: "内容价值来自真实问题"
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
    confirmation: "confirmed"
products:
  - local_id: "KP-001"
    type: "judgement_card"
    title: "内容价值判断卡"
    summary: "先看真实问题"
    payload: {"verdict":"内容必须解决真实问题","why":"没有问题就没有价值"}
    source_slice_ids: ["KS-001"]
    primary_domain: "biz_ops"
    secondary_domains: []
    source_evidence: "内容要解决真实问题"
    confirmation: "confirmed"
style_entries:
  - local_id: "ST-001"
    type: "rule"
    title: "先判断后解释"
    content: "先给一句明确判断，再解释原因和动作。"
    channels: ["wechat"]
    audiences: ["创业者"]
    purposes: ["问答"]
    source_evidence: "内容要解决真实问题"
    confirmation: "confirmed"
```
````

清洗结果至少包含一条切片、产品卡或风格条目。`local_id` 在整次导入中唯一。多行字符串使用 YAML `|` 且内容缩进六个空格；数组和对象建议使用 JSON 兼容写法。

## 4. 封闭枚举

六维：

- `values`：底层心法、价值观、原则和立场。
- `models`：思维模型、分析框架和决策结构。
- `methods`：步骤、清单、SOP 和工具。
- `concepts`：术语、定义和概念辨析。
- `facts`：经历、案例、数据和可核验事实。
- `quotes`：本人原话和高辨识度表达。

业务领域：

- `personal_ai`：个人 AI 化
- `org_ai`：企业组织 AI 化
- `ai_frontier`：AI 前沿
- `biz_ops`：商业与运营
- `opc_growth`：一人公司成长
- `heart_power`：心力提升
- `macro_trend`：宏观趋势

每条切片和产品卡必须有一个主要领域，可有 0～2 个不同的次领域。领域是召回软排序信号，不是跨领域硬过滤。

确认状态只有 `confirmed / needs_confirmation`。可见范围只有 `public / course / internal`；条目省略权限时继承素材版本，且只能收紧、不能扩大权限。

## 5. 确定性校验

导入会拒绝：

- source ID、版本、SHA、标题、权限或输入章节被修改；
- 非法维度、内容形态、领域、产品类型或风格类型；
- 主要领域与次领域重复，或次领域超过两个；
- `source_evidence` 不能在输入版本中逐字定位；
- 产品卡引用不存在的当前导入切片；
- 条目扩大素材权限或引用不存在的课程；
- 字段未知、字段重复或 `local_id` 重复；
- 待确认条目尝试审核通过或发布；
- 产品卡 `payload` 不是 JSON 对象。

幂等键：

```text
SHA256(source_id + ":" + source_version + ":" + source_sha256 + ":avatar-knowledge/v2)
```

重复导入返回原结果。需要重洗时必须在后台创建新版本。

## 6. Agent 接口

携带独立 `KNOWLEDGE_INTERNAL_TOKEN`：

```http
Authorization: Bearer <token>
```

- `GET /api/internal/v1/knowledge/sources?status=ready_for_agent`
- `GET /api/internal/v1/knowledge/sources/{source_id}/export.md`
- `POST /api/internal/v1/knowledge/imports`
- `GET /api/internal/v1/knowledge/imports/{import_id}`

接口沿用 v1 URL 以保持 Agent 连接稳定，但只交付和接收 `avatar-knowledge/v2`。旧 V1 数据保留用于回滚，不在新版接口、后台或检索中出现。

## 7. 运行时装配

问答先检索独立纯 QA；高置信命中时固定答案和图片直接输出，不经过模型。未命中时内部分类选择 2～4 个维度和相关领域，异常退回 `values + methods + facts`。

权限过滤始终先于检索。向量 Top 30 与中文词法 Top 30 使用 RRF 融合，领域只做软加权；每维最多两条，总切片最多八条，产品卡最多三条，`quotes` 最多一条。Prompt 固定按：心法定立场、模型做分析、概念做解释、方法给动作、案例做证明、原话补辨识度。

话术可额外使用最多三条风格规则或样本；风格只决定“怎么说”，不得证明事实。Embedding 故障退回词法检索，知识链故障不得影响课程、播放、后台和 webhook。
