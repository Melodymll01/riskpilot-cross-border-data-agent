/**
 * V3 案件工作台：Case / Document / Fact / Assessment Run 的人工闭环。
 */

import { ApiError, casesV3 } from "./api.js";

const $ = (selector) => document.querySelector(selector);

const state = {
  mounted: false,
  workspaces: [],
  workspaceId: "",
  caseList: [],
  caseId: "",
  caseData: null,
  documents: [],
  facts: [],
  factDetails: new Map(),
  runs: [],
  activeRun: null,
  displayRun: null,
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
    state.documents = documentData?.documents || [];
    state.facts = factData?.facts || [];
    state.factDetails = await loadFactDetails(state.facts);
    state.runs = runData?.runs || [];
    state.activeRun = pickActiveRun(state.runs);
    state.displayRun = state.activeRun || state.runs[0] || null;
    state.missingFields = state.activeRun
      ? await loadMissingFields(state.activeRun)
      : [];
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

async function proposeFacts() {
  if (!state.caseId) return;
  const fieldNames = selectedFieldNames();
  if (!fieldNames.length) {
    setStatus("error", "请先选择至少一个缺失字段");
    return;
  }
  const documentIds = state.documents
    .filter((document) => document.status === "ready")
    .map((document) => document.document_id);
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
  renderMissingFields();
  renderFacts();
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
  for (const document of state.documents) {
    const row = element("div", "case-list-row");
    const title = element("div", "case-list-main");
    title.append(
      element("strong", "", document.logical_name),
      element("span", "case-muted", document.document_type)
    );
    row.append(title, badge(document.status, document.status));
    root.appendChild(row);
  }
}

function renderRun() {
  const root = $("#case-run");
  const continueButton = $("#case-btn-continue");
  if (!root) return;
  root.replaceChildren();
  if (!state.displayRun) {
    if (continueButton) continueButton.disabled = true;
    root.appendChild(emptyNode("当前没有 Assessment Run"));
    return;
  }
  if (continueButton) {
    continueButton.disabled = !canContinueRun(state.activeRun);
  }
  const grid = element("div", "case-run-grid");
  grid.append(
    metric("Run", state.displayRun.run_id),
    metric("状态", state.displayRun.status),
    metric("阶段", state.displayRun.current_stage),
    metric("重试", String(state.displayRun.retry_count))
  );
  root.appendChild(grid);
}

function renderMissingFields() {
  const root = $("#case-missing-fields");
  const proposeButton = $("#case-btn-propose");
  if (!root) return;
  root.replaceChildren();
  if (proposeButton) {
    proposeButton.disabled =
      state.activeRun?.current_stage !== "detect_missing_facts" ||
      !state.missingFields.length;
  }
  if (!state.missingFields.length) {
    root.appendChild(
      emptyNode(
        state.activeRun?.current_stage === "detect_missing_facts"
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

function selectedFieldNames() {
  return [...document.querySelectorAll("#case-missing-fields input:checked")]
    .map((input) => input.value)
    .filter(Boolean);
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

function canContinueRun(run) {
  return !!run && new Set(["queued", "running", "waiting_for_user", "retrying"]).has(run.status);
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}
