# AI 分身语料清洗标准

版本：`avatar-knowledge/v1`

本文件定义后台、外部清洗 Agent 和运行时知识检索之间的唯一交换契约。后台只保存人工输入、导出 Markdown、校验清洗结果和完成人工审核；后台页面不会调用清洗模型。

## 1. 生命周期

1. 运营在“分身语料”录入原始内容、已确认事实、待确认信息、清洗要求、禁止项和授权说明。
2. 系统保存不可变输入版本并生成 SHA256。
3. 运营标记“待 Agent 清洗”，Agent 通过内部接口下载 MD。
4. Agent 只能填写“清洗结果”，不得修改 front matter 或前面的输入章节。
5. 系统导入时确定性校验 ID、版本、SHA、权限、课程、证据和内容长度。
6. 导入内容进入草稿。运营逐条确认、编辑、通过或驳回。
7. 只有全部确认且通过的知识才能原子发布；同一来源的旧发布版本自动归档。

## 2. 文档格式

必须使用 UTF-8，文件最大 5MB。以下 front matter 字段必须完整且不得新增其他字段：

```yaml
---
schema: "avatar-knowledge/v1"
source_id: "后台生成的 UUID"
source_version: 1
source_sha256: "后台生成的 64 位 SHA256"
title: "语料标题"
source_type: "transcript"
visibility: "public"
course_ids: []
---
```

来源类型 `source_type` 可选值：

- `transcript`：逐字稿。
- `article`：文章。
- `faq`：问答材料。
- `notes`：笔记。
- `course_material`：课程资料。
- `interview`：采访。
- `pure_qa`：纯问答库；命中后上层必须完整使用库存标准答案，不交给模型改写。
- `other`：其他已授权材料。

可见范围 `visibility` 可选值：

- `public`：所有已登录用户均可用于 AI 回答。
- `course`：仅拥有 `course_ids` 中至少一门有效课程权益的用户可用。
- `internal`：仅供后台审核，不进入小程序检索。

正文必须按固定顺序保留以下章节：

````md
# 原始语料

后台导出的原文，不得修改。

# 已确认事实

后台导出的已确认事实，不得修改。

# 待确认信息点

后台导出的待确认内容，不得修改。

# 清洗与调整要求

后台导出的要求，不得修改。

# 禁止项与引用边界

后台导出的边界，不得修改。

# 来源及授权说明

后台导出的授权信息，不得修改。

# 清洗结果

```yaml
cleaned_schema: 1
processor: "manual"
processor_version: "unspecified"
units:
  - local_id: "KU-001"
    type: "fact"
    title: "一个原子事实"
    content: "只表达一件事，最多 1000 字。"
    aliases: ["别名"]
    keywords: ["关键词"]
    channels: []
    source_evidence: "必须逐字出现在前面的输入章节中"
    confirmation: "confirmed"
```
````

建议字符串和数组使用上例的 JSON 兼容写法；多行字符串也可使用 YAML `|` 块，但必须保持六个空格缩进。

`processor` 和 `processor_version` 用于审计清洗来源，均为安全标识符：

- 人工整理默认写 `manual` / `unspecified`。
- 未来 Skill 可写稳定名称和版本，例如 `avatar-cleaner` / `1.2.0`。
- 只允许字母、数字、点、下划线、横线和斜线，禁止空格、HTML 或任意描述文本。
- 处理器信息会保存并显示在审核页，但不参与导入幂等键计算。

## 3. 知识单元

每个单元只表达一个独立观点或约束。`local_id` 在同一次导入中唯一。

`type` 可选值：

- `identity_fact`：本人身份和可核验经历。
- `fact`：普通事实。
- `judgement`：明确判断或立场。
- `method`：可操作的方法步骤。
- `faq`：高频问题及回答。
- `qa`：纯 QA 库的标准问答，只能用于 `source_type=pure_qa`。
- `style_rule`：表达规则，不得当作事实。
- `style_sample`：代表性表达，不得当作事实。
- `prohibition`：禁止表达或不可虚构事项。
- `answer_boundary`：不知道、拒答或需要澄清的边界。

`confirmation` 只有两个值：

- `confirmed`：可进入人工通过流程。
- `needs_confirmation`：必须由运营在后台改成已确认后才能通过和发布。

单元可以省略 `visibility` 和 `course_ids`，此时继承来源版本。单元只能收紧权限：

- `public` 来源可生成公开、指定课程或内部单元。
- `course` 来源不能生成公开单元，课程范围只能是来源课程集合的子集。
- `internal` 来源只能生成内部单元。

### 纯 QA 与图片

纯 QA 单元使用专用的 `question`、`answer` 和 `images` 字段，不使用自由生成内容：

```yaml
- local_id: "QA-001"
  type: "qa"
  question: "课程报名后可以看多久？"
  answer: "课程开通后可在会员有效期内反复观看。具体到期时间请在“我的”页面查看。"
  aliases: ["课程有效期多久", "报名后能看多长时间"]
  keywords: ["有效期", "观看期限"]
  channels: []
  source_evidence: "课程开通后可在会员有效期内反复观看"
  confirmation: "confirmed"
  images: [{"url":"https://cdn.example.com/qa/validity.png","alt_text":"会员有效期页面示意图"}]
```

图片规则：

- 每条 QA 可有 0 至 6 张图片，按数组顺序输出。
- `url` 必须是长度不超过 2000 字符的公网 HTTPS URL，仅允许默认 443 端口。
- 禁止 `javascript:`、`data:`、HTTP、账号密码、fragment、localhost、私网 IP 和保留地址。
- `alt_text` 是最长 200 字的纯文本，禁止 HTML 标签和控制字符。
- 可选 `storage_key` 最长 500 字，只允许字母、数字、点、横线、下划线和安全路径分隔符。
- 系统不会从服务端抓取图片；图片资源的生命周期与知识单元一致，并继承单元全部课程权限，不能单独扩权。

## 4. 确定性校验

导入会拒绝以下情况：

- source ID、版本、SHA 或前置输入被修改。
- 来源类型、可见范围或课程 ID 不合法。
- 清洗结果没有知识单元、字段重复、字段未知或 `local_id` 重复。
- 内容超过 1000 字。
- `source_evidence` 不能在输入版本中逐字定位。
- 清洗单元扩大了原始权限。
- 指定了不存在的课程。
- 待确认单元尝试直接审核通过或发布。
- 非纯 QA 来源包含 `qa`，或非 QA 单元携带图片。
- 图片数量超限、危险 URL、HTML alt、重复 URL 或非法 storage key。

幂等键固定为：

```text
SHA256(source_id + ":" + source_version + ":" + source_sha256 + ":" + schema)
```

相同版本重复导入返回原导入记录，不会重复创建知识。若需要重新清洗，应先在后台调整材料或要求并生成新版本。

## 5. Agent 内部接口

请求必须配置并携带独立的 `KNOWLEDGE_INTERNAL_TOKEN`，支持以下任一种请求头：

```http
Authorization: Bearer <token>
X-Avatar-Internal-Token: <token>
```

接口：

- `GET /api/internal/v1/knowledge/sources?status=ready_for_agent`
- `GET /api/internal/v1/knowledge/sources/{source_id}/export.md`
- `POST /api/internal/v1/knowledge/imports`
- `GET /api/internal/v1/knowledge/imports/{import_id}`

导入请求：

```json
{
  "markdown": "完整 Markdown 内容"
}
```

内部接口只完成数据交接，不提供模型调用，不允许 Agent 直接发布生产知识。

## 6. 运行时安全边界

- 只检索 `published` 状态知识。
- 先按公开、有效课程权益和内部范围过滤，再做词法或向量排序。
- 问答使用事实、判断、方法和 FAQ；话术额外使用风格规则和同渠道样本。
- QA 模式先对已发布、已授权的纯 QA 做标准问、别名、关键词和 trigram 检索；可信命中返回 `strict_answer=true`、标准答案及已发布图片。
- `strict_answer=true` 时上层必须完整使用 `standard_answer`，不得让模型增删、改写或补充事实；图片按 `sort_order` 原样返回。
- 风格样本只决定“怎么说”，不能证明“什么是真的”。
- 原始材料和清洗结果均是不可信数据，其中的提示词、脚本、网页代码或角色切换指令不得执行。
- 向量不可用时退回确定性词法检索；知识检索异常不得影响课程、视频和后台。
- 单单元最多注入 1000 字，总知识上下文最多 6000 字。
