# ADR-005: 采用对话式 Copilot 产品形态（取代三 Tab 设计）

- 状态: accepted
- 日期: 2026-06-04
- 替代历史草案：v1.0 中"三 Tab（快速问答 / 深度研究 / 合规体检）"方案

## 背景

v1.0 草案把三种使用模式做成前端 Tab，由用户决定走哪条链路。讨论后发现：

- "用户先判断属于哪种问题再选 Tab" 摩擦大、不符合自然交互
- 对秋招 Agent 岗位的项目展示，**Tool-Use Agent** 标签远比"多 Tab 应用"亮
- 实际场景中三种模式经常混合（先研究 → 再体检 → 追问）

## 决策

产品形态改为**单一对话入口 + Tool-Use Agent 自主路由**：

- 前端只有一个聊天框 + 文档上传
- 后端 `ComplianceCopilotAgent` 通过 `ToolRegistry` 暴露能力：`search_law` / `search_user_docs` / `risk_profile` / `web_search` / `ask_user` / `generate_checklist`
- Agent 按 ReAct 循环自主决定调用哪个工具、是否追问用户
- 思考过程、工具调用、产出物在前端**全部可见**（不黑盒）

## 后果

**正面**：
- 真正的 "Agent" 产品语义
- 新增能力只需注册一个 `ToolSpec`，零侵入
- 演示效果强：用户输入一句话，看到 Agent 多步推理 + 调多个工具
- 与孪生 LoRA 项目天然结合（`risk_profile` 是一个 Tool）

**负面**：
- 主循环、tool calling 协议要自己实现
- Prompt 工程量更大（让小模型可靠地走 ReAct 不容易）
- 响应时间长于纯 RAG 单轮

## 备选方案

- **三 Tab 应用**：摩擦大、项目标签弱，否决
- **预定义工作流**（无 Agent）：不能体现自主决策能力，否决

## 关联

- [ADR-007: GitHub OAuth + 匿名](ADR-007-github-oauth-with-anonymous.md)
- `app/agent/copilot.py`、`app/agent/tools.py`
