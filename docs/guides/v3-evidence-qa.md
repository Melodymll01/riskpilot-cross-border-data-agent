# V3 Evidence QA 演示指南

Evidence QA 面向低延迟、证据明确的简单问答，不使用 LangGraph：

```text
鉴权
→ 服务端构造 Scope
→ 多范围检索
→ 回读当前原文
→ 生成结构化 Claim
→ Claim-Citation 结构校验
→ 独立语义支持校验
→ 回答或拒答
```

适合：

- 解释法规概念和精确条款；
- 总结当前案件材料；
- 查询 Workspace 内部制度；
- 解释指定 Assessment 的 Finding 和 ActionItem；
- 回答有直接证据支撑的事实问题。

不适合：

- 判断完整数据出境路径；
- 生成正式综合风险评级；
- 自动批准 Assessment；
- 代替 Case Assessment Run 的人工确认与审批。

## 1. 接口

```http
POST /api/v3/qa
Content-Type: application/json
```

请求示例：

```json
{
  "question": "境外接收方有什么义务？",
  "corpora": ["case"],
  "case_id": "case_xxx",
  "top_k": 5
}
```

客户端只能声明要查询的 corpus 和资源 ID。最终 `workspace_id`、`case_id` 和
`assessment_id` 关系由服务端鉴权并重新解析，不能通过请求伪造。

## 2. 四类 Corpus

### Regulatory

```json
{
  "question": "个人信息出境需要满足什么条件？",
  "corpora": ["regulatory"]
}
```

- 只检索公共法规语料；
- 不携带当前用户私人知识库 owner；
- 引用包含法规名称、标题、原文和可选 clause ID。

### Workspace

```json
{
  "question": "公司的跨境审批制度要求什么？",
  "corpora": ["workspace"],
  "workspace_id": "ws_xxx"
}
```

- 只检索当前 Workspace 中 `document_type=workspace_knowledge` 的材料；
- 只读取 `ready` 状态的当前 DocumentVersion；
- `workspace_knowledge` 只允许 Workspace `admin` 上传；
- 普通案件材料不会自动升级为 Workspace 公共知识。

### Case

```json
{
  "question": "材料里约定了哪些境外接收方义务？",
  "corpora": ["case"],
  "case_id": "case_xxx"
}
```

- SQL 先按 `workspace_id + case_id` 过滤；
- 引用包含 Document、DocumentVersion、页码和 SHA-256；
- 回答前重新读取当前解析页；
- 若索引 quote 被篡改、文档解绑、版本已经更新或 SHA 不一致，该引用会被丢弃。

### Assessment

```json
{
  "question": "为什么这个案件被判为高风险？",
  "corpora": ["assessment"],
  "case_id": "case_xxx",
  "assessment_id": "assessment_xxx"
}
```

- Assessment 必须属于当前 Case 且对当前用户可见；
- 证据来自不可变 Assessment 的 Finding 和 ActionItem；
- 不重新运行规则，也不会修改 Assessment。

## 3. 多范围查询

```json
{
  "question": "法规和当前材料分别怎么说明境外接收方责任？",
  "corpora": ["regulatory", "case"],
  "case_id": "case_xxx",
  "top_k": 5
}
```

各范围并行检索，服务端统一去重并重新编号为 `E1`、`E2`。Case 或 Assessment Scope
会从资源关系推导 Workspace，客户端不需要重复提交 `workspace_id`。

## 4. 回答结构

```json
{
  "status": "answered",
  "answer": "1. 境外接收方应承担安全保护责任。[E1]",
  "claims": [
    {
      "claim_id": "C1",
      "text": "境外接收方应承担安全保护责任。",
      "citation_ids": ["E1"]
    }
  ],
  "citations": [
    {
      "citation_id": "E1",
      "corpus": "case",
      "document_id": "doc_xxx",
      "document_version_id": "ver_xxx",
      "page_number": 1,
      "source_sha256": "...",
      "quote": "境外接收方应承担安全保护责任"
    }
  ]
}
```

最终 `answer` 不直接使用 LLM 的自由长文本，而是由服务端基于通过校验的 Claim 渲染。

## 5. 双重 Claim-Citation 校验

### structural_v1

```json
{
  "claim_count": 1,
  "cited_claim_count": 1,
  "coverage": 1.0,
  "uncited_claim_ids": [],
  "unknown_citation_ids": [],
  "valid": true,
  "method": "structural_v1"
}
```

验证：

- 每个 Claim 是否带引用；
- 引用 ID 是否真实存在；
- 是否存在未使用引用；
- 是否实现 100% Claim 覆盖。

它不声称已经证明自然语言蕴含关系。

### independent_llm_v1

```json
{
  "judgements": [
    {
      "claim_id": "C1",
      "supported": true,
      "citation_ids": ["E1"],
      "reason": ""
    }
  ],
  "unsupported_claim_ids": [],
  "valid": true,
  "method": "independent_llm_v1"
}
```

这是独立于 Claim 生成调用的第二次校验：

- 逐个检查 Claim 是否能由引用原文直接支持；
- 不能扩大 Claim 原先声明的 citation IDs；
- 不能引用未知证据；
- 任一 Claim 不受支持时整体 fail closed。

## 6. 三种结果

### answered

所有输出 Claim 都有完整引用并通过语义支持校验。

### partially_answered

只回答现有证据能够支持的部分，并返回：

```json
{
  "unanswered_aspects": ["未找到数据保存期限"]
}
```

### refused

以下任一情况会安全拒答：

- 当前 Scope 没有证据；
- 索引原文和当前解析页不一致；
- LLM 返回非法 JSON 或非法 schema；
- Claim 没有引用或引用未知 ID；
- 独立语义验证不支持 Claim；
- 模型服务异常。

拒答结果不携带 Claim 或 Citation，避免把未经验证的中间结果暴露给用户。

## 7. 安全边界

- API 不返回 Prompt、原始模型响应或思维链；
- 用户文档只作为不可信证据进入 user message；
- Workspace/Case 权限由服务端注入；
- Regulatory 不读取用户私人 KB；
- Workspace Knowledge 不等于用户私人 KB；
- Case Citation 必须重新读取当前 DocumentVersion 原文；
- QA 不执行删除、规则发布、事实确认或 Assessment 审批；
- 完整路径与正式风险结论应启动 Assessment Run。
