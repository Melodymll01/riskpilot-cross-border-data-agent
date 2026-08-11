/**
 * V3 案件工作台：Case / Document / Fact / Assessment Run 的人工闭环。
 */

import { ApiError, casesV3 } from "./api.js";

const $ = (selector) => document.querySelector(selector);

const state = {
  mounted: false,
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
  $("#case-btn-propose")?.addEventListener("click", proposeFacts);
  $("#case-btn-continue")?.addEventListener("click", continueRun);
  $("#case-facts")?.addEventListener("click", onFactAction);
}

export async function refresh() {
  if (!state.caseId) return;
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
