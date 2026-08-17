/**
 * V3 案件工作台：Case / Document / Fact / Assessment Run 的人工闭环。
 */

import { ApiError, casesV3 } from "./api.js";

const $ = (selector) => document.querySelector(selector);
const processingDocuments = new Set();

const state = {
  mounted: false,
  workspaces: [],
  workspaceId: "",
  caseList: [],
  caseId: "",
  caseData: null,
  documents: [],
  facts: [],
  visualHits: [],
  factDetails: new Map(),
  runs: [],
  activeRun: null,
  displayRun: null,
  runDetail: null,
  missingFields: [],
};

export function mount() {
  if (state.mounted) return;
  state.mounted = true;
  $("#case-load-form")?.addEventListener("submit", onLoadCase);
  $("#case-btn-refresh")?.addEventListener("click", () => refresh());
  $("#case-workspace-select")?.addEventListener("change", onWorkspaceChange);
  $("#case-selector-list")?.addEventListener("click", onCaseSelect);
  $("#case-btn-new-workspace")?.addEventListener("click", () => openCreateModal("workspace"));
  $("#case-btn-new-case")?.addEventListener("click", () => openCreateModal("case"));
  $("#case-create-close")?.addEventListener("click", closeCreateModal);
  $("#case-create-cancel")?.addEventListener("click", closeCreateModal);
  $("#case-create-modal")?.addEventListener("click", onModalBackdrop);
  $("#case-create-form")?.addEventListener("submit", onCreateSubmit);
  $("#case-btn-propose")?.addEventListener("click", proposeFacts);
  $("#case-btn-continue")?.addEventListener("click", continueRun);
  $("#case-btn-retry-run")?.addEventListener("click", retryRun);
  $("#case-btn-cancel-run")?.addEventListener("click", cancelRun);
  $("#case-btn-review-run")?.addEventListener("click", approveRun);
  $("#case-btn-reject-run")?.addEventListener("click", rejectRun);
  document.querySelector(".case-demo-actions")?.addEventListener("click", onDemoCaseSelect);
  $("#case-upload-form")?.addEventListener("submit", uploadDocument);
  $("#case-visual-upload-form")?.addEventListener("submit", uploadVisual);
  $("#case-visual-search-form")?.addEventListener("submit", searchVisual);
  $("#case-documents")?.addEventListener("click", onDocumentAction);
  $("#case-facts")?.addEventListener("click", onFactAction);
  loadWorkspaces();
}

export async function refresh() {
  if (!state.caseId) {
    await loadWorkspaces();
    return;
  }
  setStatus("loading", "正在加载案件、材料、事实与 Run…");
  setBusy(true);
  try {
    const [caseData, documentData, factData, runData] = await Promise.all([
      casesV3.get(state.caseId),
      casesV3.documents(state.caseId),
      casesV3.facts(state.caseId),
      casesV3.runs(state.caseId),
    ]);
    state.caseData = caseData;
    state.documents = normalizeDocumentSummaries(documentData?.documents);
    state.facts = factData?.facts || [];
    state.factDetails = await loadFactDetails(state.facts);
    state.runs = runData?.runs || [];
    state.activeRun = pickActiveRun(state.runs);
    state.displayRun = state.activeRun || state.runs[0] || null;
    state.runDetail = state.displayRun
      ? await casesV3.runDetail(state.displayRun.run_id)
      : null;
    state.missingFields =
      state.runDetail?.interrupt?.missing_fact_fields ||
      (state.activeRun ? await loadMissingFields(state.activeRun) : []);
    render();
    setStatus("ok", "案件数据已刷新");
  } catch (error) {
    setStatus("error", errorMessage(error));
  } finally {
    setBusy(false);
  }
}

async function loadWorkspaces({ preserveSelection = true } = {}) {
  setStatus("loading", "正在加载 Workspace 与 Case…");
  setManagerBusy(true);
  try {
    const response = await casesV3.workspaces();
    state.workspaces = response?.workspaces || [];
    const stillExists = state.workspaces.some(
      (workspace) => workspace.workspace_id === state.workspaceId
    );
    if (!preserveSelection || !stillExists) {
      state.workspaceId = state.workspaces[0]?.workspace_id || "";
    }
    renderWorkspaceSelect();
    await loadCases(state.workspaceId);
    setStatus(
      "ok",
      state.workspaces.length ? "Workspace 与 Case 已刷新" : "尚无 Workspace，请先创建"
    );
  } catch (error) {
    setStatus("error", errorMessage(error));
  } finally {
    setManagerBusy(false);
  }
}

async function loadCases(workspaceId) {
  state.caseList = [];
  if (!workspaceId) {
    renderCaseList();
    return;
  }
  const response = await casesV3.list(workspaceId);
  state.caseList = response?.cases || [];
  renderCaseList();
}

async function onWorkspaceChange(event) {
  state.workspaceId = event.target.value;
  state.caseId = "";
  state.caseData = null;
  render();
  setManagerBusy(true);
  try {
    await loadCases(state.workspaceId);
    setStatus("ok", "Case 列表已刷新");
  } catch (error) {
    setStatus("error", errorMessage(error));
  } finally {
    setManagerBusy(false);
  }
}

async function onCaseSelect(event) {
  const button = event.target.closest("[data-case-id]");
  if (!button) return;
  state.caseId = button.dataset.caseId || "";
  const input = $("#case-id-input");
  if (input) input.value = state.caseId;
  await refresh();
}

async function onDemoCaseSelect(event) {
  const button = event.target.closest("[data-demo-case-id]");
  if (!button) return;
  state.caseId = button.dataset.demoCaseId || "";
  const input = $("#case-id-input");
  if (input) input.value = state.caseId;
  await refresh();
}

async function loadFactDetails(facts) {
  const candidates = facts.filter(
    (fact) => fact.status === "proposed" || fact.status === "conflicting"
  );
  const details = await Promise.all(
    candidates.map(async (fact) => {
      try {
        return [fact.fact_id, await casesV3.fact(fact.fact_id)];
      } catch {
        return [fact.fact_id, null];
      }
    })
  );
  return new Map(details);
}

async function onLoadCase(event) {
  event.preventDefault();
  const input = $("#case-id-input");
  const caseId = String(input?.value || "").trim();
  if (!caseId) {
    setStatus("error", "请输入 Case ID");
    return;
  }
  state.caseId = caseId;
  await refresh();
}

function openCreateModal(kind) {
  if (kind === "case" && !state.workspaceId) {
    setStatus("error", "请先创建或选择 Workspace");
    return;
  }
  const modal = $("#case-create-modal");
  const isCase = kind === "case";
  $("#case-create-kind").value = kind;
  setText("#case-create-title", isCase ? "创建 Case" : "创建 Workspace");
  setText("#case-create-name-label", isCase ? "Case 标题" : "Workspace 名称");
  document.querySelectorAll(".case-only").forEach((node) => {
    node.classList.toggle("hidden", !isCase);
  });
  $("#case-create-form")?.reset();
  $("#case-create-kind").value = kind;
  if (isCase) $("#case-create-jurisdiction").value = "CN";
  modal?.classList.remove("hidden");
  modal?.setAttribute("aria-hidden", "false");
  $("#case-create-name")?.focus();
}

function closeCreateModal() {
  const modal = $("#case-create-modal");
  modal?.classList.add("hidden");
  modal?.setAttribute("aria-hidden", "true");
}

function onModalBackdrop(event) {
  if (event.target.id === "case-create-modal") closeCreateModal();
}

async function onCreateSubmit(event) {
  event.preventDefault();
  const kind = $("#case-create-kind")?.value;
  const name = String($("#case-create-name")?.value || "").trim();
  if (!name) return;
  setCreateBusy(true);
  try {
    if (kind === "workspace") {
      const workspace = await casesV3.createWorkspace({ name });
      state.workspaceId = workspace.workspace_id;
      closeCreateModal();
      await loadWorkspaces();
      setStatus("ok", `Workspace “${workspace.name}” 已创建`);
      return;
    }
    const body = {
      workspace_id: state.workspaceId,
      title: name,
      description: String($("#case-create-description")?.value || "").trim(),
      jurisdiction: String($("#case-create-jurisdiction")?.value || "CN").trim() || "CN",
      scenario_type: String($("#case-create-scenario")?.value || "").trim(),
    };
    const assessmentDate = $("#case-create-date")?.value;
    const reviewerId = String($("#case-create-reviewer")?.value || "").trim();
    if (assessmentDate) body.assessment_date = assessmentDate;
    if (reviewerId) body.reviewer_id = reviewerId;
    const created = await casesV3.create(body);
    state.caseId = created.case_id;
    closeCreateModal();
    await loadCases(state.workspaceId);
    await refresh();
    setStatus("ok", `Case “${created.title}” 已创建`);
  } catch (error) {
    setStatus("error", errorMessage(error));
  } finally {
    setCreateBusy(false);
  }
}

async function loadMissingFields(run) {
  if (run.current_stage !== "detect_missing_facts") return [];
  const response = await casesV3.events(run.run_id);
  const events = response?.events || [];
  const event = [...events]
    .reverse()
    .find((item) => item.event_type === "fact_confirmation_required");
  return normalizeStringList(event?.payload?.missing_fact_fields);
}

async function uploadDocument(event) {
  event.preventDefault();
  if (!state.caseId) {
    setStatus("error", "请先选择 Case");
    return;
  }
  const fileInput = $("#case-upload-file");
  const file = fileInput?.files?.[0];
  if (!file) {
    setStatus("error", "请选择案件材料");
    return;
  }
  const purpose = String($("#case-upload-purpose")?.value || "").trim();
  let documentId = "";
  setDocumentControlsBusy(true);
  setStatus("loading", `正在上传并处理 ${file.name}…`);
  try {
    const uploaded = await casesV3.uploadDocument(state.caseId, file, { purpose });
    documentId = uploaded.document.document_id;
    processingDocuments.add(documentId);
    upsertDocumentSummary(uploaded.document, uploaded.job);
    renderDocuments();
    await runDocumentPipeline(uploaded.job);
    event.target.reset();
    await refresh();
    setStatus("ok", `材料 “${file.name}” 已解析并完成索引`);
  } catch (error) {
    await refreshDocuments();
    setStatus("error", `材料处理失败：${errorMessage(error)}`);
  } finally {
    if (documentId) processingDocuments.delete(documentId);
    setDocumentControlsBusy(false);
    renderDocuments();
  }
}

async function uploadVisual(event) {
  event.preventDefault();
  if (!state.caseId) {
    setStatus("error", "请先选择 Case");
    return;
  }
  const file = $("#case-visual-file")?.files?.[0];
  if (!file) {
    setStatus("error", "请选择 PNG/JPEG/WebP 图片");
    return;
  }
  const caption = String($("#case-visual-caption")?.value || "").trim();
  setVisualControlsBusy(true);
  setStatus("loading", `正在为 ${file.name} 计算 Chinese-CLIP 向量…`);
  try {
    const asset = await casesV3.uploadVisual(state.caseId, file, caption);
    event.target.reset();
    setStatus("ok", `图片 “${asset.filename}” 已入库，可使用文本搜图`);
    const queryInput = $("#case-visual-query");
    if (queryInput && caption) queryInput.value = caption;
  } catch (error) {
    setStatus("error", `图片上传失败：${errorMessage(error)}`);
  } finally {
    setVisualControlsBusy(false);
  }
}

async function searchVisual(event) {
  event.preventDefault();
  if (!state.caseId) return;
  const query = String($("#case-visual-query")?.value || "").trim();
  if (!query) return;
  setVisualControlsBusy(true);
  setStatus("loading", "正在当前 Case 中执行文本搜图…");
  try {
    const response = await casesV3.searchVisual(state.caseId, query, 6);
    state.visualHits = response?.hits || [];
    renderVisualHits();
    setStatus("ok", `找到 ${state.visualHits.length} 张相关图片`);
  } catch (error) {
    setStatus("error", `图片检索失败：${errorMessage(error)}`);
  } finally {
    setVisualControlsBusy(false);
  }
}

async function onDocumentAction(event) {
  const button = event.target.closest("[data-document-action]");
  if (!button) return;
  const action = button.dataset.documentAction;
  const jobId = button.dataset.jobId;
  const documentId = button.dataset.documentId;
  if (!action || !jobId || !documentId || processingDocuments.has(documentId)) return;
  processingDocuments.add(documentId);
  renderDocuments();
  setStatus("loading", action === "retry" ? "正在重试材料处理…" : "正在继续材料处理…");
  try {
    let job = await casesV3.processingJob(jobId);
    if (action === "retry") {
      job = await casesV3.retryDocument(jobId);
    }
    updateDocumentJob(documentId, job);
    renderDocuments();
    await runDocumentPipeline(job);
    await refresh();
    setStatus("ok", "材料已完成解析与索引");
  } catch (error) {
    await refreshDocuments();
    setStatus("error", `材料处理失败：${errorMessage(error)}`);
  } finally {
    processingDocuments.delete(documentId);
    renderDocuments();
  }
}

async function runDocumentPipeline(job) {
  let currentJob = job;
  if (currentJob.status === "failed") {
    throw new Error(currentJob.error_message || "处理任务失败，请重试");
  }
  if (currentJob.status === "queued") {
    const parsed = await casesV3.parseDocument(currentJob.job_id);
    currentJob = parsed.job;
    upsertDocumentSummary(parsed.document, currentJob);
    renderDocuments();
  }
  if (currentJob.status === "running" && currentJob.current_stage === "ocr") {
    throw new Error("当前材料需要 OCR，服务端尚未提供 OCR 执行阶段");
  }
  if (currentJob.status === "running" && currentJob.current_stage === "chunk") {
    const indexed = await casesV3.indexDocument(currentJob.job_id);
    currentJob = indexed.job;
    upsertDocumentSummary(indexed.document, currentJob);
    renderDocuments();
  }
  if (currentJob.status !== "completed") {
    throw new Error(
      `任务停留在 ${currentJob.status}/${currentJob.current_stage}，请刷新后继续`
    );
  }
  return currentJob;
}

async function refreshDocuments() {
  if (!state.caseId) return;
  try {
    const response = await casesV3.documents(state.caseId);
    state.documents = normalizeDocumentSummaries(response?.documents);
    renderDocuments();
  } catch {}
}

async function proposeFacts() {
  if (!state.caseId) return;
  const fieldNames = selectedFieldNames();
  if (!fieldNames.length) {
    setStatus("error", "请先选择至少一个缺失字段");
    return;
  }
  const documentIds = state.documents
    .filter((item) => item.document.status === "ready")
    .map((item) => item.document.document_id);
  if (!documentIds.length) {
    setStatus("error", "当前案件没有 ready 文档，无法生成 Fact 候选");
    return;
  }
  setStatus("loading", "正在从案件文档生成 Fact 候选…");
  setBusy(true);
  try {
    const batch = await casesV3.proposeFacts(state.caseId, {
      field_names: fieldNames,
      document_ids: documentIds,
    });
    const count = batch?.facts?.length || 0;
    const conflicts = batch?.conflict_field_names || [];
    const suffix = conflicts.length
      ? `；检测到冲突字段：${conflicts.join("、")}`
      : "";
    setStatus("ok", `已生成 ${count} 个候选${suffix}`);
    await refresh();
  } catch (error) {
    setStatus("error", errorMessage(error));
  } finally {
    setBusy(false);
  }
}

async function onFactAction(event) {
  const button = event.target.closest("[data-fact-action]");
  if (!button) return;
  const factId = button.dataset.factId;
  const action = button.dataset.factAction;
  if (!factId || action !== "confirm") return;
  setStatus("loading", "正在确认 Fact…");
  setBusy(true);
  try {
    await casesV3.transitionFact(factId, "confirmed");
    setStatus("ok", "Fact 已确认；同字段其他 active facts 已自动拒绝");
    await refresh();
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) {
      setStatus("error", "当前账号无确认权限，请切换 Reviewer 或 Admin");
    } else {
      setStatus("error", errorMessage(error));
    }
  } finally {
    setBusy(false);
  }
}

async function continueRun() {
  if (!state.activeRun) {
    setStatus("error", "当前案件没有可继续的活动 Run");
    return;
  }
  setStatus("loading", "正在重新检查事实并继续 Assessment Run…");
  setBusy(true);
  try {
    await casesV3.continueRun(state.activeRun.run_id);
    setStatus("ok", "Run 已继续");
    await refresh();
  } catch (error) {
    setStatus("error", errorMessage(error));
  } finally {
    setBusy(false);
  }
}

async function retryRun() {
  if (!state.displayRun || !state.runDetail?.actions?.can_retry) return;
  await runAction("正在重试失败的 Run…", () =>
    casesV3.retryRun(state.displayRun.run_id)
  );
}

async function cancelRun() {
  if (!state.displayRun || !state.runDetail?.actions?.can_cancel) return;
  if (!window.confirm("确认取消当前 Assessment Run？")) return;
  await runAction("正在取消 Run…", () =>
    casesV3.cancelRun(state.displayRun.run_id)
  );
}

async function approveRun() {
  if (!state.displayRun || !state.runDetail?.actions?.can_review) return;
  await runAction("正在提交 Reviewer 审批…", () =>
    casesV3.reviewRun(state.displayRun.run_id, {
      decision: "approved",
      comment: "Run Detail 页面演示审批通过",
    })
  );
}

async function rejectRun() {
  if (!state.displayRun || !state.runDetail?.actions?.can_review) return;
  const comment = window.prompt("请输入拒绝原因");
  if (!comment?.trim()) return;
  await runAction("正在提交 Reviewer 拒绝意见…", () =>
    casesV3.reviewRun(state.displayRun.run_id, {
      decision: "rejected",
      comment: comment.trim(),
    })
  );
}

async function runAction(loadingMessage, action) {
  setStatus("loading", loadingMessage);
  setBusy(true);
  try {
    await action();
    await refresh();
    setStatus("ok", "Run 状态已更新");
  } catch (error) {
    setStatus("error", errorMessage(error));
  } finally {
    setBusy(false);
  }
}

function render() {
  $("#case-empty")?.classList.toggle("hidden", !!state.caseData);
  $("#case-content")?.classList.toggle("hidden", !state.caseData);
  if (!state.caseData) return;
  setText("#case-title", state.caseData.title);
  setText("#case-status-badge", state.caseData.status);
  setText("#case-workspace", state.caseData.workspace_id);
  setText("#case-reviewer", state.caseData.reviewer_id || "未指定");
  renderDocuments();
  renderRun();
  renderRunDetail();
  renderMissingFields();
  renderFacts();
  renderVisualHits();
}

function renderWorkspaceSelect() {
  const select = $("#case-workspace-select");
  if (!select) return;
  select.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = state.workspaces.length
    ? "请选择 Workspace"
    : "暂无 Workspace";
  select.appendChild(placeholder);
  for (const workspace of state.workspaces) {
    const option = document.createElement("option");
    option.value = workspace.workspace_id;
    option.textContent = workspace.name;
    option.selected = workspace.workspace_id === state.workspaceId;
    select.appendChild(option);
  }
}

function renderCaseList() {
  const root = $("#case-selector-list");
  if (!root) return;
  root.replaceChildren();
  if (!state.workspaceId) {
    root.appendChild(emptyNode("请先选择 Workspace"));
    return;
  }
  if (!state.caseList.length) {
    root.appendChild(emptyNode("当前 Workspace 尚无 Case"));
    return;
  }
  for (const item of state.caseList) {
    const button = element("button", "case-selector-item");
    button.type = "button";
    button.dataset.caseId = item.case_id;
    button.classList.toggle("is-active", item.case_id === state.caseId);
    const text = element("span", "case-list-main");
    text.append(
      element("strong", "", item.title),
      element("span", "case-muted", item.case_id)
    );
    button.append(text, badge(item.status, item.status));
    root.appendChild(button);
  }
}

function renderDocuments() {
  const root = $("#case-documents");
  if (!root) return;
  root.replaceChildren();
  if (!state.documents.length) {
    root.appendChild(emptyNode("尚未上传案件材料"));
    return;
  }
  for (const summary of state.documents) {
    const documentData = summary.document;
    const job = summary.latest_job;
    const row = element("article", "case-document-card");
    const header = element("div", "case-list-row");
    const title = element("div", "case-list-main");
    title.append(
      element("strong", "", documentData.logical_name),
      element("span", "case-muted", documentData.document_type)
    );
    header.append(title, badge(documentData.status, documentData.status));
    row.appendChild(header);
    if (job) {
      const progress = document.createElement("progress");
      progress.className = "case-document-progress";
      progress.max = 100;
      progress.value = Math.round(job.progress * 100);
      const meta = element(
        "span",
        "case-muted",
        `阶段：${job.current_stage} · 进度：${progress.value}% · 重试：${job.retry_count}`
      );
      row.append(meta, progress);
      if (job.status === "failed") {
        row.appendChild(
          element(
            "p",
            "case-document-error",
            job.error_message || job.error_code || "材料处理失败"
          )
        );
      }
      const action = documentAction(job);
      if (action) {
        const actions = element("div", "case-document-actions");
        const button = element(
          "button",
          action === "retry" ? "btn btn-ghost" : "btn btn-primary",
          action === "retry" ? "重试处理" : "继续处理"
        );
        button.type = "button";
        button.dataset.documentAction = action;
        button.dataset.documentId = documentData.document_id;
        button.dataset.jobId = job.job_id;
        button.disabled = processingDocuments.has(documentData.document_id);
        actions.appendChild(button);
        row.appendChild(actions);
      }
    } else {
      row.appendChild(element("p", "case-document-error", "当前版本缺少处理任务"));
    }
    root.appendChild(row);
  }
}

function renderRun() {
  const root = $("#case-run");
  const continueButton = $("#case-btn-continue");
  const retryButton = $("#case-btn-retry-run");
  const cancelButton = $("#case-btn-cancel-run");
  const reviewButton = $("#case-btn-review-run");
  const rejectButton = $("#case-btn-reject-run");
  if (!root) return;
  root.replaceChildren();
  if (!state.displayRun) {
    if (continueButton) continueButton.disabled = true;
    if (retryButton) retryButton.disabled = true;
    if (cancelButton) cancelButton.disabled = true;
    if (reviewButton) reviewButton.disabled = true;
    if (rejectButton) rejectButton.disabled = true;
    root.appendChild(emptyNode("当前没有 Assessment Run"));
    return;
  }
  const actions = state.runDetail?.actions || {};
  if (continueButton) continueButton.disabled = !actions.can_continue;
  if (retryButton) retryButton.disabled = !actions.can_retry;
  if (cancelButton) cancelButton.disabled = !actions.can_cancel;
  if (reviewButton) reviewButton.disabled = !actions.can_review;
  if (rejectButton) rejectButton.disabled = !actions.can_review;
  const grid = element("div", "case-run-grid");
  grid.append(
    metric("Run", state.displayRun.run_id),
    metric("状态", state.displayRun.status),
    metric("阶段", state.displayRun.current_stage),
    metric("重试", String(state.displayRun.retry_count)),
    metric("Token", String(state.displayRun.token_usage)),
    metric(
      "Cost",
      `${formatCost(state.displayRun.cost)} ${state.runDetail?.cost_currency || "unspecified"}`
    ),
    metric("耗时", formatDuration(state.runDetail?.duration_ms || 0)),
    metric("Revision", String(state.displayRun.revision))
  );
  root.appendChild(grid);
}

function renderRunDetail() {
  renderEvidencePlan();
  renderTimeline();
  renderInterrupt();
  renderToolCalls();
  renderVerification();
  renderAssessment();
}

function renderEvidencePlan() {
  const root = $("#case-run-plan");
  if (!root) return;
  root.replaceChildren();
  const plan = state.runDetail?.evidence_plan;
  if (!plan) {
    root.appendChild(emptyNode("Evidence Plan 尚未生成"));
    return;
  }
  const groups = [
    ["调查问题", plan.investigation_questions],
    ["必需事实", plan.required_fact_fields],
    ["计划工具", plan.planned_tools],
    ["证据缺口", plan.evidence_gaps],
    ["完成标准", plan.completion_criteria],
  ];
  for (const [title, values] of groups) {
    const group = element("div", "case-plan-group");
    group.appendChild(element("strong", "", title));
    const list = element("ul", "case-plan-list");
    for (const value of values || []) list.appendChild(element("li", "", value));
    if (!list.children.length) list.appendChild(element("li", "case-muted", "无"));
    group.appendChild(list);
    root.appendChild(group);
  }
}

function renderTimeline() {
  const root = $("#case-run-timeline");
  if (!root) return;
  root.replaceChildren();
  const timeline = state.runDetail?.timeline || [];
  if (!timeline.length) {
    root.appendChild(emptyNode("暂无运行事件"));
    return;
  }
  for (const item of timeline) {
    const row = element("li", "case-timeline-item");
    row.dataset.status = item.status;
    const marker = element("span", "case-timeline-marker", String(item.sequence));
    const content = element("div", "case-timeline-content");
    const header = element("div", "case-timeline-head");
    header.append(
      element("strong", "", item.stage || item.event_type),
      badge(item.status, item.status)
    );
    content.append(
      header,
      element("span", "case-muted", item.summary),
      element(
        "span",
        "case-timeline-meta",
        `${formatDuration(item.duration_ms)} · ${formatTimestamp(item.created_at)}`
      )
    );
    row.append(marker, content);
    root.appendChild(row);
  }
}

function renderInterrupt() {
  const root = $("#case-run-interrupt");
  const wrapper = $("#case-run-interrupt-wrap");
  if (!root || !wrapper) return;
  root.replaceChildren();
  const interrupt = state.runDetail?.interrupt;
  wrapper.classList.toggle("hidden", !interrupt);
  if (!interrupt) return;
  root.append(
    badge("waiting_for_user", interrupt.kind),
    element("p", "case-interrupt-reason", interrupt.reason)
  );
  appendChips(root, "缺失事实", interrupt.missing_fact_fields);
  appendChips(root, "冲突字段", interrupt.conflict_field_names);
  appendChips(root, "候选 Fact", interrupt.candidate_fact_ids);
}

function renderToolCalls() {
  const root = $("#case-run-tools");
  if (!root) return;
  root.replaceChildren();
  const calls = state.runDetail?.tool_calls || [];
  if (!calls.length) {
    root.appendChild(emptyNode("暂无工具调用"));
    return;
  }
  for (const call of calls) {
    const details = document.createElement("details");
    details.className = "case-tool-call";
    const summary = document.createElement("summary");
    summary.append(
      element("strong", "", call.tool_name),
      element(
        "span",
        "case-muted",
        `${formatDuration(call.duration_ms)} · retry ${call.retry_count} · token ${call.token_usage}`
      )
    );
    const body = element("div", "case-tool-body");
    body.append(
      labeledCode("参数", call.arguments),
      labeledCode("结果", call.output),
      element("p", "case-muted", call.result_summary || "工具执行完成")
    );
    details.append(summary, body);
    root.appendChild(details);
  }
}

function renderVerification() {
  const root = $("#case-run-verification");
  if (!root) return;
  root.replaceChildren();
  const rule = state.runDetail?.rule_evaluation;
  const citation = state.runDetail?.citation_verification;
  if (!rule && !citation) {
    root.appendChild(emptyNode("尚未执行规则或 Citation 校验"));
    return;
  }
  if (rule) root.appendChild(labeledCode("确定性规则", rule));
  if (citation) root.appendChild(labeledCode("Claim-Citation", citation));
}

function renderAssessment() {
  const root = $("#case-run-assessment");
  if (!root) return;
  root.replaceChildren();
  const bundle = state.runDetail?.assessment;
  if (!bundle) {
    root.appendChild(emptyNode("当前 Run 尚未生成 Assessment"));
    return;
  }
  const assessment = bundle.assessment;
  const grid = element("div", "case-assessment-grid");
  grid.append(
    metric("Assessment", assessment.assessment_id),
    metric("状态", assessment.status),
    metric("风险", assessment.risk_level),
    metric("路径", (assessment.candidate_paths || []).join("、") || "无"),
    metric("Finding", String(bundle.findings?.length || 0)),
    metric("Citation", String(bundle.evidence_citations?.length || 0))
  );
  root.appendChild(grid);
  for (const finding of bundle.findings || []) {
    root.appendChild(
      element(
        "div",
        "case-assessment-finding",
        `${finding.severity.toUpperCase()} · ${finding.title}`
      )
    );
  }
}

function renderMissingFields() {
  const root = $("#case-missing-fields");
  const proposeButton = $("#case-btn-propose");
  if (!root) return;
  root.replaceChildren();
  if (proposeButton) {
    proposeButton.disabled =
      state.activeRun?.current_stage !== "human_fact_confirmation" ||
      !state.missingFields.length;
  }
  if (!state.missingFields.length) {
    root.appendChild(
      emptyNode(
        state.activeRun?.current_stage === "human_fact_confirmation"
          ? "事件中未找到缺失字段"
          : "当前 Run 不在 Fact Confirmation 阶段"
      )
    );
    return;
  }
  for (const fieldName of state.missingFields) {
    const label = element("label", "case-check");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = fieldName;
    input.checked = true;
    label.append(input, document.createTextNode(fieldName));
    root.appendChild(label);
  }
}

function renderFacts() {
  const root = $("#case-facts");
  if (!root) return;
  root.replaceChildren();
  if (!state.facts.length) {
    root.appendChild(emptyNode("当前案件尚无 Fact"));
    return;
  }
  for (const fact of state.facts) {
    const card = element("article", "case-fact-card");
    const header = element("div", "case-fact-header");
    const title = element("div", "case-list-main");
    title.append(
      element("strong", "", fact.field_name),
      element("span", "case-muted", `v${fact.version} · ${fact.source_type}`)
    );
    header.append(title, badge(fact.status, fact.status));
    card.append(header);
    card.append(element("pre", "case-fact-value", formatValue(fact.value)));
    const detail = state.factDetails.get(fact.fact_id);
    for (const evidence of detail?.evidence || []) {
      const evidenceNode = element("div", "case-evidence");
      evidenceNode.append(
        element(
          "span",
          "case-evidence-source",
          `${evidence.document_id} · 第 ${evidence.page_number} 页`
        ),
        element("q", "case-evidence-quote", evidence.quote)
      );
      card.appendChild(evidenceNode);
    }
    const actions = element("div", "case-fact-actions");
    if (fact.status === "proposed" || fact.status === "conflicting") {
      const confirm = element("button", "btn btn-primary", "Reviewer 确认");
      confirm.type = "button";
      confirm.dataset.factAction = "confirm";
      confirm.dataset.factId = fact.fact_id;
      actions.appendChild(confirm);
    }
    card.appendChild(actions);
    root.appendChild(card);
  }
}

function renderVisualHits() {
  const root = $("#case-visual-results");
  if (!root) return;
  root.replaceChildren();
  if (!state.visualHits.length) {
    root.appendChild(emptyNode("上传图片后输入自然语言进行检索"));
    return;
  }
  for (const hit of state.visualHits) {
    const card = element("article", "case-visual-hit");
    const image = document.createElement("img");
    image.loading = "lazy";
    image.alt = hit.asset.caption || hit.asset.filename;
    image.src = casesV3.visualContentUrl(state.caseId, hit.asset.asset_id);
    const meta = element("div", "case-visual-meta");
    meta.append(
      element("strong", "", hit.asset.caption || hit.asset.filename),
      element("span", "case-visual-score", `相似度 ${Number(hit.score).toFixed(3)}`),
      element("span", "case-muted", `${hit.asset.width}×${hit.asset.height}`)
    );
    card.append(image, meta);
    root.appendChild(card);
  }
}

function selectedFieldNames() {
  return [...document.querySelectorAll("#case-missing-fields input:checked")]
    .map((input) => input.value)
    .filter(Boolean);
}

function appendChips(root, label, values) {
  if (!values?.length) return;
  const group = element("div", "case-chip-group");
  group.appendChild(element("span", "case-muted", label));
  const chips = element("div", "case-chips");
  for (const value of values) chips.appendChild(element("span", "case-chip", value));
  group.appendChild(chips);
  root.appendChild(group);
}

function labeledCode(label, value) {
  const wrapper = element("div", "case-code-block");
  wrapper.append(
    element("span", "case-muted", label),
    element("pre", "case-fact-value", JSON.stringify(value || {}, null, 2))
  );
  return wrapper;
}

function formatDuration(value) {
  const duration = Number(value) || 0;
  if (duration >= 1000) return `${(duration / 1000).toFixed(2)}s`;
  return `${Math.round(duration)}ms`;
}

function formatCost(value) {
  const cost = Number(value) || 0;
  return cost === 0 ? "0" : cost.toFixed(6);
}

function formatTimestamp(value) {
  const timestamp = Number(value);
  if (!timestamp) return "—";
  return new Date(timestamp * 1000).toLocaleString("zh-CN", { hour12: false });
}

function pickActiveRun(runs) {
  const active = new Set([
    "queued",
    "running",
    "waiting_for_user",
    "waiting_for_review",
    "retrying",
  ]);
  return runs.find((run) => active.has(run.status)) || null;
}

function normalizeStringList(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item) => typeof item === "string" && item.trim()))];
}

function normalizeDocumentSummaries(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item) => item?.document_id)
    .map((item) => ({
      document: {
        document_id: item.document_id,
        workspace_id: item.workspace_id,
        logical_name: item.logical_name,
        document_type: item.document_type,
        status: item.status,
        created_by: item.created_by,
        current_version_id: item.current_version_id,
        created_at: item.created_at,
        updated_at: item.updated_at,
      },
      latest_job: item.latest_job || null,
    }));
}

function upsertDocumentSummary(documentData, job) {
  const summary = { document: documentData, latest_job: job };
  const index = state.documents.findIndex(
    (item) => item.document.document_id === documentData.document_id
  );
  if (index >= 0) {
    state.documents[index] = summary;
  } else {
    state.documents.unshift(summary);
  }
}

function updateDocumentJob(documentId, job) {
  const summary = state.documents.find(
    (item) => item.document.document_id === documentId
  );
  if (summary) summary.latest_job = job;
}

function documentAction(job) {
  if (!job) return "";
  if (job.status === "failed") return "retry";
  if (job.status === "queued") return "continue";
  if (job.status === "running" && job.current_stage === "chunk") return "continue";
  return "";
}

function setBusy(busy) {
  for (const selector of [
    "#case-btn-refresh",
    "#case-btn-propose",
    "#case-btn-continue",
    "#case-load-submit",
  ]) {
    const button = $(selector);
    if (button) button.disabled = busy;
  }
  if (!busy) {
    const continueButton = $("#case-btn-continue");
    const proposeButton = $("#case-btn-propose");
    if (continueButton) {
      continueButton.disabled = !canContinueRun(state.activeRun);
    }
    if (proposeButton) {
      proposeButton.disabled =
        state.activeRun?.current_stage !== "detect_missing_facts" ||
        !state.missingFields.length;
    }
  }
  setDocumentControlsBusy(busy);
  setVisualControlsBusy(busy);
}

function setManagerBusy(busy) {
  for (const selector of [
    "#case-workspace-select",
    "#case-btn-new-workspace",
    "#case-btn-new-case",
  ]) {
    const node = $(selector);
    if (node) node.disabled = busy;
  }
}

function setCreateBusy(busy) {
  const button = $("#case-create-submit");
  if (button) button.disabled = busy;
}

function setDocumentControlsBusy(busy) {
  for (const selector of ["#case-upload-file", "#case-upload-purpose", "#case-upload-submit"]) {
    const node = $(selector);
    if (node) node.disabled = busy;
  }
}

function setVisualControlsBusy(busy) {
  for (const selector of [
    "#case-visual-file",
    "#case-visual-caption",
    "#case-visual-query",
    "#case-visual-upload-form button",
    "#case-visual-search-form button",
  ]) {
    const node = $(selector);
    if (node) node.disabled = busy;
  }
}

function setStatus(kind, message) {
  const node = $("#case-status");
  if (!node) return;
  node.dataset.state = kind;
  node.textContent = message;
}

function setText(selector, text) {
  const node = $(selector);
  if (node) node.textContent = text ?? "";
}

function badge(status, text) {
  const node = element("span", "case-badge", text);
  node.dataset.status = status || "unknown";
  return node;
}

function metric(label, value) {
  const node = element("div", "case-metric");
  node.append(
    element("span", "case-metric-label", label),
    element("strong", "case-metric-value", value)
  );
  return node;
}

function emptyNode(message) {
  return element("div", "case-empty-inline", message);
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = String(text);
  return node;
}

function formatValue(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}


function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}
