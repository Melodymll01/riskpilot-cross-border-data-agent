# 离线法规建库

在提问前完成本地法规整理、切分和向量入库。在线 Agent 的 `search_law` 读取这个公共法规索引；`search_user_docs` 继续读取用户授权材料。

工具入口：`services/agent-runtime/scripts/build_regulatory_kb.py`。它读取当前 `.env` 的参数，不做参数搜索，不调用聊天模型或远端 Embedding API，也不抓取清单里的 URL。本地向量模型必须事先下载好；模型不在缓存时明确失败。

## 当前参数

本轮采用冻结的本地 BGE 配置；切分参数已用同一批法规问题做过对比评测：

| 参数 | 值 |
| --- | --- |
| 文本切分 / 重叠 | 420 / 84，单位是字符 |
| 章节结构 | `parent-child-v1`；子块向量化，父章节按 metadata 虚拟重建 |
| Embedding 输入 | `section_path + 子块正文`，总长度仍限制为 420 字符 |
| Top-K | 5 |
| 向量模型 | BAAI/bge-small-zh-v1.5，实际维度由建库时核验，当前为 512 |
| 检索融合 | 向量 + BM25 + RRF，k=60，两个权重均为 1 |
| 查询改写 / reranker | 关闭 / 关闭 |
| 普通问题上下文 | 相邻片段窗口 1 |
| 流程问题上下文 | 同一一级章节扩展；单章节 3600 字符、全局 12000 字符上限 |
| 候选池 | 向量 `Top-K × 4`、BM25 `Top-K × 6`，各自最多 100 条 |
| 单块 Embedding token 上限 | 480；BGE-small 模型上限 512，保留特殊 token 余量 |

`EMBEDDING_DIMENSIONS=2048` 属于 API 向量配置，在当前本地 BGE 模式下不作为实际输出维度。入库报告同时记录模型、切分参数、token 上限和真实向量维度。

## 准备来源清单

例如将文件与清单放在同一个法规目录：

```text
regulations/
  manifest.json
  raw/
    pipl-2021.txt
```

`manifest.json` 示例（标题、版本和地址应按实际取得的原文填写）：

```json
{
  "schema_version": 1,
  "corpus_id": "cn-cross-border-v1",
  "scope": "本次明确选定的中国数据出境法规清单",
  "documents": [
    {
      "document_id": "pipl-2021",
      "path": "raw/pipl-2021.txt",
      "title": "中华人民共和国个人信息保护法",
      "source_url": "https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm",
      "version": "2021-08-20 发布文本",
      "jurisdiction": "CN"
    }
  ]
}
```

- `document_id` 必须唯一，不同版本使用不同 ID。
- 路径相对清单目录，解析后的文件路径必须位于该目录内。
- 支持 UTF-8 TXT/Markdown、PDF、DOCX，以及包含字符串 `text` 字段的 JSON 文件。
- 可提供 `sha256` 校验既有来源文件；无论是否提供，工具都会计算并冻结本地文件的 SHA256。
- `clause_id` 可用于明确的单条摘录；全文不得随意标成一个条号。
- 清单定义这次收录范围。工具不会判定清单是否涵盖全部法律，也不会自动判定版本的现行有效性；版本更新需要整理新的来源清单并重建。

## 准备、建库与接入

只检查文件并切分，便于先审阅文本和参数：

```bash
.venv/bin/python services/agent-runtime/scripts/build_regulatory_kb.py \
  --manifest /绝对路径/regulations/manifest.json \
  --output data/regulatory_builds/prepared-v1 --prepare-only
```

正式建库使用另一个新目录：

```bash
.venv/bin/python services/agent-runtime/scripts/build_regulatory_kb.py \
  --manifest /绝对路径/regulations/manifest.json \
  --output data/regulatory_builds/cn-cross-border-v1
```

产物包含：`sources/` 原始文件快照、`manifest.lock.json` 来源与参数清单、`chunks.jsonl` 文本块、`build_report.json` 验证结果，以及完成建库后的 `chroma/`。输出目录已经存在时直接拒绝覆盖；失败产物保留 `status=failed`、`ready_for_query=false`，可用于诊断，不能作为就绪索引。

确认报告为 `status=completed`、`ready_for_query=true` 后，将 `.env` 的 `CHROMA_PERSIST_DIR` 指向该产物的 `chroma/`，保持同一 BGE 模型配置并重启应用。法规索引切换只需要调整 Chroma 路径，会话、账号记忆和附件数据库仍使用原配置。

仓库内的 `assets/demo/regulatory_manifest.json` 只包含第十九条摘录，可用于验证命令；它不代表正式法规语料，工具也不会自动把示例索引切换为界面使用的索引。

本次工具验证：完整门禁 920 passed、1 skipped；示例通过真实本地 BGE 建库与混合检索，实际向量维度 512，命中记录保留来源版本、条号、URL 和本地来源文件 SHA256。模型权重已预先缓存，建库和检索验证均禁用了 Hugging Face 联网。切换 Embedding 模型、维度、token 上限或切分参数时，必须使用新的 Chroma 目录并重建索引。
