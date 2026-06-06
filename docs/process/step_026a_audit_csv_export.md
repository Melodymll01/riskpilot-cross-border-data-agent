# Step 026a — Admin 审计日志 CSV 导出

> Step 021 / 023a 把审计日志的存储 + admin UI 通了一遍；本步补上「拿走」
> 这一环：admin 在审计面板点「⬇ CSV」即可把当前过滤条件下的全部记录下载
> 为 Excel 可直读的 CSV，便于离线归档 / 合规审查 / BI 分析。

## 1. 目标

- 后端新增 `GET /api/v2/audit/export.csv`，admin-only，流式输出 CSV
- 复用既有 `action` / `actor_id` 过滤参数语义（与 `/audit/logs` 一致）
- 一次导出有硬上限（`max_rows` ≤ 10000），防 admin 误触把内存打爆
- timestamp 双列：ISO 8601 UTC + Unix epoch（方便 Excel 既能看也能排序 / diff）
- 中文字段（`extra_json` 等）UTF-8 + BOM，Excel 双击不乱码
- 前端审计面板 toolbar 加「⬇ CSV」按钮，按当前过滤条件触发原生下载流

## 2. 改动清单

### 后端 1 文件 + 1 测试

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `api/v2/audit.py` | 修改 | 新增 `GET /audit/export.csv` + `_stream_csv` / `_flush` / `_entry_to_row` helper |
| `tests/api/test_audit.py` | 修改 | 加 `TestExportCsv`（7 用例：401 / 403 / 200+headers / 过滤 action / 过滤 actor / 空表只有表头 / max_rows 校验） |

### 前端 3 文件

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `frontend/api.js` | 修改 | `audit` 命名空间加 `exportCsvUrl()`，返回 URL 字符串（不发请求） |
| `frontend/admin-audit.js` | 修改 | `bindUI()` 末尾加 `#audit-btn-export` 点击事件，构造 `<a download>` 触发浏览器原生下载 |
| `frontend/index.html` | 修改 | toolbar 增「⬇ CSV」按钮（紧邻刷新按钮） |

## 3. 关键决策

### D1 — `StreamingResponse` + `csv.writer`，不一次性 list

- `list_recent(limit=max_rows)` 已经把全部行拉到内存（受 max_rows 上限保护）
- 但**序列化**这一步用 `io.StringIO` + `csv.writer` 分块 yield，每
  `CSV_FLUSH_EVERY=200` 行 flush 一次推给客户端
- 好处：客户端能看到下载进度（chunked transfer），admin 网慢也不卡白屏
- 进一步演化方向：当行数过万时把 `list_recent` 也改成 `iter_recent`
  generator，端口需要扩协议——本步先不动，等真正出现内存压力再升级

### D2 — 暂不引入 `since` / `until` 时间范围

- 当前 admin 场景：导一次留底 / 合规复盘；过滤已经有 action + actor_id 两维度
- 时间范围需要：扩 `AuditLogPort` 协议 + `SqliteAuditLogRepo` SQL + Fake repo +
  前端日期选择控件 → 4 处改动，体量不属于「最小可用」
- 留 **Step 026b 候选**：要做时一起做完前后端 + 测试

### D3 — `max_rows` 硬上限 10000

| 候选 | 理由 |
| --- | --- |
| 不设上限 | admin 误操作（写 max_rows=10_000_000）会瞬间打爆 SQLite + 内存 |
| 1000 | 小到不够用；线上累计 1 周很可能就破 |
| **10000** ✅ | 经验值；按每行 ~500 bytes 算总负载 ~5MB，Excel / Pandas 都能秒开 |
| 100000 | 5 万行起 Excel 打开明显卡顿，admin 没有合理用例 |

需要更多行：分多次按 action / actor_id 过滤后合并；或加 time range
（Step 026b）后按月切片。

### D4 — timestamp 双列：ISO + epoch

仅 ISO：Excel 排序需要先转成日期格式，admin 容易踩
仅 epoch：人类不可读，纯审计场景体验差
**双列**：两边都讨好，多一列 + ~20 bytes/行 的代价完全可以接受

`timestamp_iso` 用 `datetime.fromtimestamp(ts, tz=UTC).isoformat(timespec="milliseconds")`，
固定 UTC 时区——避免容器时区漂移导致同一份导出在不同环境结果不一致。

### D5 — `extra_json` 列保留原 JSON 字符串

- 不展开：因为 schema 不固定（`{size: 12}` / `{reason: "中文"}` / `{}` 都合法），
  按 key 展开列会让 CSV 列数不稳定，下游 BI 脚本崩
- 不解码：保留 `json.dumps(..., ensure_ascii=False)` 输出原文，下游用
  `pandas.json_normalize` 二次解析按需展开

### D6 — UTF-8 BOM 前缀

Windows Excel 默认按 GBK 读 CSV，看到中文 `extra_json={"reason": "中文"}` 会乱码。
解决方案：响应体起头 yield `b"\xef\xbb\xbf"`（UTF-8 BOM），Excel 据此自动按
UTF-8 解码。
其他客户端（VS Code、`csv` 模块、`pandas.read_csv`）都能识别 BOM，无副作用。

测试侧用 `body.decode("utf-8-sig")` 自动剥 BOM，与 Excel 行为一致。

### D7 — 前端用 `<a download>` 触发原生下载，不走 fetch

- 走 fetch：CSV 整个被读进 JS 内存 → 再 `URL.createObjectURL(blob)` →
  下载。10000 行 ~5MB 直接进堆，浏览器内存峰值翻倍
- 走 `<a download>`：浏览器走原生流式下载通道，CSV 永远不进 JS 堆，无内存压力
- 文件名靠服务端 `Content-Disposition: attachment; filename="audit_export_*.csv"`

## 4. 验收

- 后端测试：`tests/api/test_audit.py::TestExportCsv` 全 7 用例绿
- 整轮回归：`pytest -q` 全绿（基线 597 → 597 + 7 = 604）
- 手动验收：
  1. admin 登录 → 审计面板 → 点「⬇ CSV」→ 浏览器下载 `audit_export_*.csv`
  2. 用 Excel 打开 → 中文不乱码 → 时间列按 ISO 字符串排序正确
  3. 选「action=kb.delete」过滤 → 再点「⬇ CSV」→ CSV 只有 delete 记录
  4. 非 admin（anon / github user）curl 调 `/audit/export.csv` → 403

## 5. 未做 / 后续

- Step 026b 候选：`since` / `until` 时间范围过滤（后端 Port + SQL + 前端日期选择）
- Step 026c 候选：审计日志归档自动化（定时 export + S3 上传 + 本地表清理）
- 不在本步：admin UI 的「按时间倒序排序」UI 切换（当前服务端固定 DESC）
