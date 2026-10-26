const SVG_NS = "http://www.w3.org/2000/svg";
const FLOW_SELECTOR = "pre > code.language-riskpilot-flow";
const NODE_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
const CITATION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$/;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;
const NO_LINE_START_PATTERN = /[，。！？；：、）》】」』”’…,.!?;:)\]}]/u;
const V2_NODE_KINDS = new Set(["start", "action", "decision", "merge", "terminal"]);

const LIMITS = Object.freeze({
  sourceLength: 128 * 1024,
  titleLength: 200,
  v1LabelLength: 2000,
  v2LabelLength: 240,
  conditionLength: 80,
  edgeLabelLength: 240,
  citationIdLength: 100,
  citationsPerItem: 24,
  nodes: 24,
  edges: 48,
});

const NODE_WIDTH = 300;
const NODE_GAP = 76;
const CHART_PADDING = 48;
const LABEL_LINE_HEIGHT = 18;
const CITATION_LINE_HEIGHT = 15;
const EDGE_LINE_HEIGHT = 15;

function hasExactKeys(value, expected) {
  const keys = Object.keys(value);
  return keys.length === expected.length
    && expected.every(key => Object.prototype.hasOwnProperty.call(value, key));
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isBoundedText(value, maxLength) {
  return typeof value === "string"
    && value.length > 0
    && value.length <= maxLength
    && value.trim().length > 0
    && !CONTROL_CHARACTER_PATTERN.test(value);
}

function isOptionalBoundedText(value, maxLength) {
  return typeof value === "string"
    && value.length <= maxLength
    && !CONTROL_CHARACTER_PATTERN.test(value)
    && (value.length === 0 || value.trim().length > 0);
}

function normalizeFlowSemantics(value) {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[\p{P}\p{S}\s]+/gu, "");
}

function validCitationIds(value, { required = false, strictId = false } = {}) {
  return Array.isArray(value)
    && (!required || value.length > 0)
    && value.length <= LIMITS.citationsPerItem
    && value.every(item => (
      isBoundedText(item, LIMITS.citationIdLength)
      && (!strictId || CITATION_ID_PATTERN.test(item))
    ))
    && new Set(value).size === value.length;
}

function analyseGraph(nodes, edges) {
  const indegree = new Map(nodes.map(node => [node.id, 0]));
  const adjacency = new Map(nodes.map(node => [node.id, []]));
  const predecessors = new Map(nodes.map(node => [node.id, []]));
  const levels = new Map(nodes.map(node => [node.id, 0]));
  for (const edge of edges) {
    adjacency.get(edge.from).push(edge.to);
    predecessors.get(edge.to).push(edge.from);
    indegree.set(edge.to, indegree.get(edge.to) + 1);
  }

  const remainingIndegree = new Map(indegree);
  const queue = nodes.filter(node => remainingIndegree.get(node.id) === 0).map(node => node.id);
  let visited = 0;
  for (let index = 0; index < queue.length; index += 1) {
    const nodeId = queue[index];
    visited += 1;
    for (const targetId of adjacency.get(nodeId)) {
      levels.set(targetId, Math.max(levels.get(targetId), levels.get(nodeId) + 1));
      remainingIndegree.set(targetId, remainingIndegree.get(targetId) - 1);
      if (remainingIndegree.get(targetId) === 0) queue.push(targetId);
    }
  }
  if (visited !== nodes.length) return null;

  const outdegree = new Map(nodes.map(node => [node.id, adjacency.get(node.id).length]));
  return { adjacency, indegree, levels, outdegree, predecessors };
}

function validGraphSize(value) {
  return Array.isArray(value.nodes)
    && value.nodes.length >= 2
    && value.nodes.length <= LIMITS.nodes
    && Array.isArray(value.edges)
    && value.edges.length >= 1
    && value.edges.length <= LIMITS.edges;
}

function parseV1(value) {
  const nodeIds = new Set();
  const nodes = [];
  for (const node of value.nodes) {
    if (
      !isRecord(node)
      || !hasExactKeys(node, ["id", "label", "citation_ids"])
      || typeof node.id !== "string"
      || !NODE_ID_PATTERN.test(node.id)
      || nodeIds.has(node.id)
      || !isBoundedText(node.label, LIMITS.v1LabelLength)
      || !validCitationIds(node.citation_ids)
    ) {
      return null;
    }
    nodeIds.add(node.id);
    nodes.push({ ...node, kind: "action" });
  }

  const edgeKeys = new Set();
  const edges = [];
  for (const edge of value.edges) {
    const edgeKey = `${edge?.from}\u0000${edge?.to}`;
    if (
      !isRecord(edge)
      || !hasExactKeys(edge, ["from", "to", "label", "citation_ids"])
      || typeof edge.from !== "string"
      || typeof edge.to !== "string"
      || !NODE_ID_PATTERN.test(edge.from)
      || !NODE_ID_PATTERN.test(edge.to)
      || edge.from === edge.to
      || !nodeIds.has(edge.from)
      || !nodeIds.has(edge.to)
      || edgeKeys.has(edgeKey)
      || !isBoundedText(edge.label, LIMITS.v1LabelLength)
      || !validCitationIds(edge.citation_ids)
    ) {
      return null;
    }
    edgeKeys.add(edgeKey);
    edges.push({
      from: edge.from,
      to: edge.to,
      condition: edge.label,
      claim: "",
      citation_ids: edge.citation_ids,
    });
  }

  const graph = analyseGraph(nodes, edges);
  if (!graph) return null;
  return {
    flow: { ...value, nodes, edges },
    graph,
  };
}

function validateV2Topology(nodes, edges, graph) {
  const starts = nodes.filter(node => node.kind === "start");
  const terminals = nodes.filter(node => node.kind === "terminal");
  if (starts.length !== 1 || terminals.length < 1) return false;

  for (const node of nodes) {
    const incoming = graph.indegree.get(node.id);
    const outgoing = graph.outdegree.get(node.id);
    if (node.kind === "start" && (incoming !== 0 || outgoing < 1)) return false;
    if (node.kind === "terminal" && outgoing !== 0) return false;
    if (node.kind === "decision") {
      if (outgoing < 2) return false;
      const conditions = edges
        .filter(edge => edge.from === node.id)
        .map(edge => normalizeFlowSemantics(edge.branchCondition));
      if (conditions.some(condition => !condition)) return false;
      if (new Set(conditions).size !== conditions.length) return false;
    }
    if (node.kind === "merge" && (incoming < 2 || outgoing > 1)) return false;
  }

  const reachable = new Set();
  const pending = [starts[0].id];
  while (pending.length) {
    const nodeId = pending.pop();
    if (reachable.has(nodeId)) continue;
    reachable.add(nodeId);
    pending.push(...graph.adjacency.get(nodeId));
  }
  return reachable.size === nodes.length;
}

function parseV2(value) {
  const nodeIds = new Set();
  const nodes = [];
  for (const node of value.nodes) {
    if (
      !isRecord(node)
      || !hasExactKeys(node, ["id", "kind", "label", "citation_ids"])
      || typeof node.id !== "string"
      || !NODE_ID_PATTERN.test(node.id)
      || nodeIds.has(node.id)
      || typeof node.kind !== "string"
      || !V2_NODE_KINDS.has(node.kind)
      || !isBoundedText(node.label, LIMITS.v2LabelLength)
      || !validCitationIds(node.citation_ids, { required: true, strictId: true })
    ) {
      return null;
    }
    nodeIds.add(node.id);
    nodes.push({ ...node });
  }

  const edgeKeys = new Set();
  const edges = [];
  for (const edge of value.edges) {
    if (
      !isRecord(edge)
      || !hasExactKeys(edge, ["from", "to", "condition", "label", "citation_ids"])
      || typeof edge.from !== "string"
      || typeof edge.to !== "string"
      || !NODE_ID_PATTERN.test(edge.from)
      || !NODE_ID_PATTERN.test(edge.to)
      || edge.from === edge.to
      || !nodeIds.has(edge.from)
      || !nodeIds.has(edge.to)
      || !isOptionalBoundedText(edge.condition, LIMITS.conditionLength)
      || !isBoundedText(edge.label, LIMITS.edgeLabelLength)
      || !validCitationIds(edge.citation_ids, { required: true, strictId: true })
    ) {
      return null;
    }
    const edgeKey = `${edge.from}\u0000${edge.to}\u0000${normalizeFlowSemantics(edge.condition)}`;
    if (edgeKeys.has(edgeKey)) return null;
    edgeKeys.add(edgeKey);
    const condition = edge.condition.trim();
    edges.push({
      from: edge.from,
      to: edge.to,
      branchCondition: condition,
      condition: condition || edge.label,
      claim: condition && normalizeFlowSemantics(condition) !== normalizeFlowSemantics(edge.label)
        ? edge.label
        : "",
      citation_ids: edge.citation_ids,
    });
  }

  const graph = analyseGraph(nodes, edges);
  if (!graph || !validateV2Topology(nodes, edges, graph)) return null;
  applyGenericDisplayLabels(nodes, edges, graph);
  return {
    flow: { ...value, nodes, edges },
    graph,
  };
}

function compactDisplayText(value, maxLength = 22) {
  const text = String(value || "").replace(/\s+/gu, " ").trim();
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 1).trimEnd()}…`;
}

function applyGenericDisplayLabels(nodes, edges, graph) {
  const nodeById = new Map(nodes.map(node => [node.id, node]));
  const originalLabels = new Map(nodes.map(node => [node.id, node.label]));
  for (const edge of edges) {
    edge.fullCondition = edge.condition;
    edge.condition = compactDisplayText(edge.condition, 20);
  }
  for (const node of nodes) {
    const original = originalLabels.get(node.id) || node.label;
    node.fullLabel = original;
    if (node.kind === "decision") {
      const conditions = edges
        .filter(edge => edge.from === node.id)
        .map(edge => edge.condition)
        .filter(Boolean);
      node.label = conditions.length
        ? compactDisplayText(`分支判断：${conditions.join(" / ")}`, 100)
        : "分支判断";
    } else if (node.kind === "merge") {
      node.label = `分支汇合：${compactDisplayText(original, 42)}`;
    } else if (node.kind === "terminal") {
      const predecessorLabels = graph.predecessors.get(node.id)
        .map(id => originalLabels.get(id) || nodeById.get(id)?.label || "");
      if (predecessorLabels.some(label => normalizeFlowSemantics(label) === normalizeFlowSemantics(original))) {
        node.label = "流程结束";
      }
    }
  }
}

function parseFlow(source) {
  if (typeof source !== "string" || source.length > LIMITS.sourceLength) return null;
  let value;
  try {
    value = JSON.parse(source);
  } catch {
    return null;
  }
  if (
    !isRecord(value)
    || !isBoundedText(value.title, LIMITS.titleLength)
    || !validGraphSize(value)
  ) {
    return null;
  }
  if (value.schema_version === "1.0") {
    return hasExactKeys(value, ["schema_version", "title", "nodes", "edges"])
      ? parseV1(value)
      : null;
  }
  if (value.schema_version === "2.0") {
    return hasExactKeys(value, ["schema_version", "title", "coverage", "nodes", "edges"])
      && ["complete", "partial"].includes(value.coverage)
      ? parseV2(value)
      : null;
  }
  return null;
}

function visualWidth(character) {
  return /[^\u0000-\u00ff]/u.test(character) ? 2 : 1;
}

function textUnits(value) {
  return Array.from(value).reduce((total, character) => total + visualWidth(character), 0);
}

function wrapText(value, maxUnits) {
  const text = value.replace(/\s+/gu, " ").trim();
  const lines = [];
  let line = "";
  let units = 0;
  for (const character of Array.from(text)) {
    const width = visualWidth(character);
    if (line && units + width > maxUnits && !NO_LINE_START_PATTERN.test(character)) {
      lines.push(line.trimEnd());
      line = "";
      units = 0;
    }
    line += character;
    units += width;
  }
  if (line) lines.push(line.trimEnd());
  return lines.length ? lines : [""];
}

function citationLines(citationIds, maxUnits) {
  if (!citationIds.length) return [];
  return wrapText(citationIds.map(citationId => `[${citationId}]`).join(" "), maxUnits);
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function appendTextLines(
  element,
  lines,
  x,
  startY,
  lineHeight,
  className = null,
  anchor = null,
) {
  lines.forEach((line, index) => {
    const tspan = svgElement("tspan", { x, y: startY + index * lineHeight });
    if (className) tspan.setAttribute("class", className);
    if (anchor) tspan.setAttribute("text-anchor", anchor);
    tspan.textContent = line;
    element.appendChild(tspan);
  });
}

function prepareNode(node) {
  const labelUnits = node.kind === "decision" ? 24 : node.kind === "merge" ? 18 : 36;
  const citationUnits = node.kind === "decision" ? 30 : node.kind === "merge" ? 18 : 42;
  const labelLines = wrapText(node.label, labelUnits);
  const citations = citationLines(node.citation_ids, citationUnits);
  const contentHeight = 42 + labelLines.length * LABEL_LINE_HEIGHT
    + (citations.length ? 8 + citations.length * CITATION_LINE_HEIGHT : 0);
  let height = Math.max(76, contentHeight);
  if (node.kind === "decision") height = Math.max(146, contentHeight + 34);
  if (node.kind === "merge") height = Math.max(64, contentHeight - 10);
  return { ...node, labelLines, citationLines: citations, height };
}

function prepareEdge(edge) {
  const conditionLines = wrapText(edge.condition, 30);
  const claimLines = edge.claim ? wrapText(edge.claim, 42) : [];
  const citations = citationLines(edge.citation_ids, 42);
  const allLines = [...conditionLines, ...claimLines, ...citations];
  const maximumUnits = Math.max(1, ...allLines.map(textUnits));
  const labelWidth = Math.max(96, Math.min(310, maximumUnits * 6.2 + 24));
  const labelHeight = 14
    + conditionLines.length * EDGE_LINE_HEIGHT
    + (claimLines.length ? 5 + claimLines.length * EDGE_LINE_HEIGHT : 0)
    + (citations.length ? 4 + citations.length * CITATION_LINE_HEIGHT : 0)
    + 9;
  return {
    ...edge,
    conditionLines,
    claimLines,
    citationLines: citations,
    labelWidth,
    labelHeight,
  };
}

function orderRanks(nodes, ranks, graph) {
  const originalOrder = new Map(nodes.map((node, index) => [node.id, index]));
  const positions = new Map();
  ranks.forEach((rank, level) => {
    if (level > 0) {
      rank.sort((left, right) => {
        const leftSources = graph.predecessors.get(left.id).map(id => positions.get(id) ?? 0);
        const rightSources = graph.predecessors.get(right.id).map(id => positions.get(id) ?? 0);
        const leftMean = leftSources.reduce((sum, value) => sum + value, 0) / leftSources.length;
        const rightMean = rightSources.reduce((sum, value) => sum + value, 0) / rightSources.length;
        return leftMean - rightMean || originalOrder.get(left.id) - originalOrder.get(right.id);
      });
    }
    rank.forEach((node, index) => positions.set(node.id, index));
  });
}

function assignNodeGeometry(node) {
  node.centerX = node.x + NODE_WIDTH / 2;
  node.shapeX = node.x;
  node.shapeY = node.y;
  node.shapeWidth = NODE_WIDTH;
  node.shapeHeight = node.height;
  if (node.kind === "start") {
    node.shapeX += 28;
    node.shapeWidth -= 56;
  }
  if (node.kind === "merge") {
    node.mergeCenterX = node.centerX - 54;
    node.mergeCenterY = node.y + node.height / 2;
    node.portX = node.mergeCenterX;
    node.portTop = node.mergeCenterY - 12;
    node.portBottom = node.mergeCenterY + 12;
    return;
  }
  node.portX = node.centerX;
  node.portTop = node.shapeY;
  node.portBottom = node.shapeY + node.shapeHeight;
}

function prepareLayout(flow, graph) {
  const nodes = flow.nodes.map(prepareNode);
  const nodeById = new Map(nodes.map(node => [node.id, node]));
  const edges = flow.edges.map(prepareEdge);
  const maximumLevel = Math.max(...graph.levels.values());
  const ranks = Array.from({ length: maximumLevel + 1 }, () => []);
  for (const node of nodes) {
    node.level = graph.levels.get(node.id);
    ranks[node.level].push(node);
  }
  orderRanks(nodes, ranks, graph);

  const longEdges = edges.filter(
    edge => graph.levels.get(edge.to) - graph.levels.get(edge.from) > 1,
  );
  const sideGutter = longEdges.length
    ? 150 + Math.ceil(longEdges.length / 2) * 22
    : CHART_PADDING;
  const widestRank = Math.max(...ranks.map(rank => rank.length));
  const innerWidth = widestRank * NODE_WIDTH + Math.max(0, widestRank - 1) * NODE_GAP;
  const width = Math.max(720, innerWidth + sideGutter * 2);
  const rankHeights = ranks.map(rank => Math.max(...rank.map(node => node.height)));
  const maximumEdgeLabelHeight = Math.max(...edges.map(edge => edge.labelHeight));
  const rankGap = Math.max(132, maximumEdgeLabelHeight + 50);

  const rankTops = [];
  let cursorY = CHART_PADDING;
  rankHeights.forEach(height => {
    rankTops.push(cursorY);
    cursorY += height + rankGap;
  });
  const height = cursorY - rankGap + CHART_PADDING;

  ranks.forEach((rank, level) => {
    const rankWidth = rank.length * NODE_WIDTH + Math.max(0, rank.length - 1) * NODE_GAP;
    let cursorX = (width - rankWidth) / 2;
    for (const node of rank) {
      node.x = cursorX;
      node.y = rankTops[level] + (rankHeights[level] - node.height) / 2;
      assignNodeGeometry(node);
      cursorX += NODE_WIDTH + NODE_GAP;
    }
  });

  let leftLane = 0;
  let rightLane = 0;
  longEdges.forEach((edge, index) => {
    const source = nodeById.get(edge.from);
    const target = nodeById.get(edge.to);
    const preferLeft = (source.portX + target.portX) / 2 < width / 2
      || (source.portX === target.portX && index % 2 === 0);
    edge.routing = "side";
    edge.side = preferLeft ? "left" : "right";
    if (preferLeft) {
      edge.routeX = CHART_PADDING + leftLane * 22;
      leftLane += 1;
    } else {
      edge.routeX = width - CHART_PADDING - rightLane * 22;
      rightLane += 1;
    }
  });

  return { nodes, nodeById, edges, width, height };
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function edgeRoute(edge, source, target, layoutWidth) {
  const sourceX = source.portX;
  const sourceY = source.portBottom;
  const targetX = target.portX;
  const targetY = target.portTop;
  if (edge.routing === "side") {
    const routeX = edge.routeX;
    const labelX = edge.side === "left"
      ? routeX + edge.labelWidth / 2 + 14
      : routeX - edge.labelWidth / 2 - 14;
    return {
      path: `M ${sourceX} ${sourceY} V ${sourceY + 24} H ${routeX} V ${targetY - 24} H ${targetX} V ${targetY - 10}`,
      labelX: clamp(labelX, edge.labelWidth / 2 + 6, layoutWidth - edge.labelWidth / 2 - 6),
      labelY: (sourceY + targetY) / 2,
      targetX,
      targetY,
      routing: "side",
    };
  }

  const middleY = (sourceY + targetY) / 2;
  let labelX = (sourceX + targetX) / 2;
  if (Math.abs(sourceX - targetX) < 1) {
    const rightCandidate = sourceX + edge.labelWidth / 2 + 18;
    labelX = rightCandidate + edge.labelWidth / 2 < layoutWidth
      ? rightCandidate
      : sourceX - edge.labelWidth / 2 - 18;
  }
  const path = Math.abs(sourceX - targetX) < 1
    ? `M ${sourceX} ${sourceY} V ${targetY - 10}`
    : `M ${sourceX} ${sourceY} V ${middleY} H ${targetX} V ${targetY - 10}`;
  return {
    path,
    labelX: clamp(labelX, edge.labelWidth / 2 + 6, layoutWidth - edge.labelWidth / 2 - 6),
    labelY: middleY,
    targetX,
    targetY,
    routing: "layered",
  };
}

function appendEdgeLabel(group, edge, route) {
  const label = svgElement("g", { class: "risk-flow-edge-label" });
  const left = route.labelX - edge.labelWidth / 2;
  const top = route.labelY - edge.labelHeight / 2;
  const box = svgElement("rect", {
    class: "risk-flow-edge-label-box",
    x: left,
    y: top,
    width: edge.labelWidth,
    height: edge.labelHeight,
    rx: 8,
  });
  const condition = svgElement("text", { class: "risk-flow-edge-condition" });
  let cursorY = top + 18;
  appendTextLines(
    condition,
    edge.conditionLines,
    route.labelX,
    cursorY,
    EDGE_LINE_HEIGHT,
    null,
    "middle",
  );
  cursorY += edge.conditionLines.length * EDGE_LINE_HEIGHT;
  label.append(box, condition);

  if (edge.claimLines.length) {
    cursorY += 5;
    const claim = svgElement("text", { class: "risk-flow-edge-claim" });
    appendTextLines(
      claim,
      edge.claimLines,
      left + 12,
      cursorY,
      EDGE_LINE_HEIGHT,
    );
    cursorY += edge.claimLines.length * EDGE_LINE_HEIGHT;
    label.appendChild(claim);
  }
  if (edge.citationLines.length) {
    cursorY += 4;
    const citations = svgElement("text", { class: "risk-flow-edge-citations" });
    appendTextLines(
      citations,
      edge.citationLines,
      left + 12,
      cursorY,
      CITATION_LINE_HEIGHT,
    );
    label.appendChild(citations);
  }
  group.appendChild(label);
}

function appendEdge(svg, edge, source, target, layoutWidth) {
  const route = edgeRoute(edge, source, target, layoutWidth);
  const group = svgElement("g", {
    class: "risk-flow-edge",
    "data-from": edge.from,
    "data-to": edge.to,
    "data-routing": route.routing,
  });
  const title = svgElement("title");
  const fullCondition = edge.fullCondition || edge.condition;
  title.textContent = edge.claim
    ? `${fullCondition}：${edge.claim}`
    : fullCondition;
  const path = svgElement("path", {
    class: "risk-flow-edge-path",
    d: route.path,
  });
  const arrow = svgElement("polygon", {
    class: "risk-flow-edge-arrow",
    points: `${route.targetX},${route.targetY - 2} ${route.targetX - 5},${route.targetY - 12} ${route.targetX + 5},${route.targetY - 12}`,
  });
  group.append(title, path, arrow);
  appendEdgeLabel(group, edge, route);
  svg.appendChild(group);
}

function appendStandardNodeContent(group, node, { inset = 16 } = {}) {
  const id = svgElement("text", {
    class: "risk-flow-node-id",
    x: node.shapeX + inset,
    y: node.shapeY + 22,
  });
  id.textContent = node.id;
  const label = svgElement("text", { class: "risk-flow-node-label" });
  appendTextLines(
    label,
    node.labelLines,
    node.shapeX + inset,
    node.shapeY + 47,
    LABEL_LINE_HEIGHT,
  );
  group.append(id, label);
  if (node.citationLines.length) {
    const citations = svgElement("text", { class: "risk-flow-node-citations" });
    appendTextLines(
      citations,
      node.citationLines,
      node.shapeX + inset,
      node.shapeY + 54 + node.labelLines.length * LABEL_LINE_HEIGHT,
      CITATION_LINE_HEIGHT,
    );
    group.appendChild(citations);
  }
}

function appendDecisionNode(group, node) {
  const centerY = node.y + node.height / 2;
  const points = [
    `${node.centerX},${node.y}`,
    `${node.x + NODE_WIDTH},${centerY}`,
    `${node.centerX},${node.y + node.height}`,
    `${node.x},${centerY}`,
  ].join(" ");
  group.appendChild(svgElement("polygon", {
    class: "risk-flow-node-box risk-flow-node-shape risk-flow-node-decision",
    points,
  }));
  const totalLines = 1 + node.labelLines.length + node.citationLines.length;
  let cursorY = centerY - ((totalLines - 1) * LABEL_LINE_HEIGHT) / 2;
  const id = svgElement("text", { class: "risk-flow-node-id" });
  appendTextLines(id, [node.id], node.centerX, cursorY, LABEL_LINE_HEIGHT, null, "middle");
  cursorY += LABEL_LINE_HEIGHT + 2;
  const label = svgElement("text", { class: "risk-flow-node-label" });
  appendTextLines(
    label,
    node.labelLines,
    node.centerX,
    cursorY,
    LABEL_LINE_HEIGHT,
    null,
    "middle",
  );
  group.append(id, label);
  if (node.citationLines.length) {
    cursorY += node.labelLines.length * LABEL_LINE_HEIGHT + 3;
    const citations = svgElement("text", { class: "risk-flow-node-citations" });
    appendTextLines(
      citations,
      node.citationLines,
      node.centerX,
      cursorY,
      CITATION_LINE_HEIGHT,
      null,
      "middle",
    );
    group.appendChild(citations);
  }
}

function appendMergeNode(group, node) {
  const size = 12;
  const x = node.mergeCenterX;
  const y = node.mergeCenterY;
  group.appendChild(svgElement("polygon", {
    class: "risk-flow-node-box risk-flow-node-shape risk-flow-node-merge",
    points: `${x},${y - size} ${x + size},${y} ${x},${y + size} ${x - size},${y}`,
  }));
  const textX = x + 25;
  const contentLines = 1 + node.labelLines.length + node.citationLines.length;
  let cursorY = y - ((contentLines - 1) * LABEL_LINE_HEIGHT) / 2;
  const id = svgElement("text", { class: "risk-flow-node-id", x: textX, y: cursorY });
  id.textContent = node.id;
  cursorY += LABEL_LINE_HEIGHT;
  const label = svgElement("text", { class: "risk-flow-node-label" });
  appendTextLines(label, node.labelLines, textX, cursorY, LABEL_LINE_HEIGHT);
  group.append(id, label);
  if (node.citationLines.length) {
    cursorY += node.labelLines.length * LABEL_LINE_HEIGHT + 2;
    const citations = svgElement("text", { class: "risk-flow-node-citations" });
    appendTextLines(citations, node.citationLines, textX, cursorY, CITATION_LINE_HEIGHT);
    group.appendChild(citations);
  }
}

function appendNode(svg, node) {
  const group = svgElement("g", {
    class: "risk-flow-node",
    "data-node-id": node.id,
    "data-kind": node.kind,
  });
  const title = svgElement("title");
  title.textContent = node.fullLabel || node.label;
  group.appendChild(title);

  if (node.kind === "decision") {
    appendDecisionNode(group, node);
  } else if (node.kind === "merge") {
    appendMergeNode(group, node);
  } else {
    const rx = node.kind === "start" ? node.shapeHeight / 2 : 12;
    group.appendChild(svgElement("rect", {
      class: `risk-flow-node-box risk-flow-node-shape risk-flow-node-${node.kind}`,
      x: node.shapeX,
      y: node.shapeY,
      width: node.shapeWidth,
      height: node.shapeHeight,
      rx,
    }));
    if (node.kind === "terminal") {
      group.appendChild(svgElement("rect", {
        class: "risk-flow-node-terminal-inner",
        x: node.shapeX + 5,
        y: node.shapeY + 5,
        width: node.shapeWidth - 10,
        height: node.shapeHeight - 10,
        rx: 8,
      }));
    }
    appendStandardNodeContent(group, node, { inset: node.kind === "start" ? 28 : 16 });
  }
  svg.appendChild(group);
}

function buildFlowchart(flow, graph) {
  const layout = prepareLayout(flow, graph);
  const figure = document.createElement("figure");
  figure.className = "risk-flowchart";
  figure.dataset.schemaVersion = flow.schema_version;
  if (flow.schema_version === "2.0") figure.dataset.coverage = flow.coverage;

  const caption = document.createElement("figcaption");
  caption.className = "risk-flowchart-title";
  const captionText = document.createElement("span");
  captionText.textContent = flow.title;
  caption.appendChild(captionText);
  if (flow.schema_version === "2.0" && flow.coverage === "partial") {
    const badge = document.createElement("span");
    badge.className = "risk-flowchart-coverage-badge";
    badge.textContent = "部分覆盖";
    caption.appendChild(badge);
  }
  const scroller = document.createElement("div");
  scroller.className = "risk-flowchart-scroll";
  const svg = svgElement("svg", {
    class: "risk-flowchart-svg",
    viewBox: `0 0 ${layout.width} ${layout.height}`,
    width: layout.width,
    height: layout.height,
    role: "img",
    "aria-label": flow.title,
    preserveAspectRatio: "xMidYMin meet",
  });

  for (const edge of layout.edges) {
    appendEdge(
      svg,
      edge,
      layout.nodeById.get(edge.from),
      layout.nodeById.get(edge.to),
      layout.width,
    );
  }
  for (const node of layout.nodes) appendNode(svg, node);
  scroller.appendChild(svg);
  figure.append(caption, scroller);
  return figure;
}

export function renderRiskPilotFlowchart(codeElement) {
  if (!codeElement?.matches?.("code.language-riskpilot-flow")) return false;
  const pre = codeElement.parentElement;
  if (!pre || pre.tagName !== "PRE") return false;
  const parsed = parseFlow(codeElement.textContent || "");
  if (!parsed) return false;
  try {
    const flowchart = buildFlowchart(parsed.flow, parsed.graph);
    pre.replaceWith(flowchart);
    return true;
  } catch {
    return false;
  }
}

export function renderRiskPilotFlowcharts(root) {
  if (!root?.querySelectorAll) return 0;
  let rendered = 0;
  for (const codeElement of Array.from(root.querySelectorAll(FLOW_SELECTOR))) {
    if (renderRiskPilotFlowchart(codeElement)) rendered += 1;
  }
  return rendered;
}
