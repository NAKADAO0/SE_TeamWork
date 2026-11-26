import { escapeHtml as h } from "../shared/dom.js";

export function graphFeedback(run, stage, history = false) {
  const groups = new Map();
  for (const item of run.feedback ?? []) {
    if (
      item.scope !== "internal" ||
      (item.stage_id ?? item.target_stage) !== stage
    )
      continue;
    const source = item.source_node,
      target = item.target_node;
    if (!source || !target) continue;
    const active =
      item.resolved === false &&
      stage === run.stage &&
      ["running", "pausing", "paused", "failed"].includes(run.status) &&
      item.epoch === (run.stages[stage]?.epoch ?? 1);
    if (!history && !active) continue;
    const key = source + ":" + target;
    const previous = groups.get(key);
    groups.set(key, {
      source,
      target,
      count: (previous?.count ?? 0) + 1,
      active: active || previous?.active,
      reason: item.reason ?? "",
      round: item.round_after,
    });
  }
  return [...groups.values()];
}
export function executionGraph(flow, feedback, width, status, selected) {
  const count = flow.nodes.length;
  const horizontalWidth = count * 150 + (count - 1) * 32 + 24;
  const vertical = width < horizontalWidth;
  const nodeWidth = vertical ? Math.max(170, Math.min(340, width - 90)) : 150,
    nodeHeight = 70;
  const gap = vertical ? 38 : 32;
  const graphWidth = vertical ? Math.max(240, width) : horizontalWidth;
  const mainHeight = vertical ? count * (nodeHeight + gap) - gap + 24 : 100;
  const graphHeight = mainHeight + (vertical ? 0 : feedback.length * 38 + 8);
  const positions = new Map(
    flow.nodes.map((node, index) => [
      node.id,
      vertical
        ? [64, 12 + index * (nodeHeight + gap)]
        : [12 + index * (nodeWidth + gap), 12],
    ]),
  );
  const arrows = flow.edges
    .filter((edge) => edge.type !== "feedback")
    .map((edge) => {
      const a = positions.get(edge.source),
        b = positions.get(edge.target);
      if (!a || !b) return "";
      const path = vertical
        ? `M${a[0] + nodeWidth / 2} ${a[1] + nodeHeight} V${b[1]}`
        : `M${a[0] + nodeWidth} ${a[1] + nodeHeight / 2} H${b[0]}`;
      return `<path d="${path}" class="execution-forward" marker-end="url(#execution-next)"/>`;
    })
    .join("");
  const loops = feedback
    .map((edge, index) => {
      const a = positions.get(edge.source),
        b = positions.get(edge.target);
      if (!a || !b) return "";
      const lane = vertical ? 14 + index * 14 : mainHeight + index * 38 + 12;
      const path = vertical
        ? `M${a[0]} ${a[1] + nodeHeight / 2} H${lane} V${b[1] + nodeHeight / 2} H${b[0]}`
        : `M${a[0] + nodeWidth / 2 + index * 5} ${a[1] + nodeHeight} V${lane} H${b[0] + nodeWidth / 2 - index * 5} V${b[1] + nodeHeight}`;
      return `<path d="${path}" class="execution-return ${edge.active ? "active" : "historical"}" marker-end="url(#execution-${edge.active ? "return" : "history"})"/>`;
    })
    .join("");
  return `<div class="execution-ordered ${vertical ? "vertical" : "horizontal"}" style="width:${graphWidth}px;height:${graphHeight}px"><svg viewBox="0 0 ${graphWidth} ${graphHeight}" width="${graphWidth}" height="${graphHeight}" aria-hidden="true"><defs>${[
    ["next", "#aeb9c7"],
    ["return", "#ac6b26"],
    ["history", "#9ca6b4"],
  ]
    .map(
      ([id, color]) =>
        `<marker id="execution-${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10" fill="${color}"/></marker>`,
    )
    .join("")}</defs>${arrows}${loops}</svg>${flow.nodes
    .map((node, index) => {
      const [x, y] = positions.get(node.id);
      return `<button type="button" class="flow-node execution-node" data-node="${h(node.id)}" data-status="${h(status(node.id))}" aria-pressed="${selected === node.id}" style="--node-x:${x}px;--node-y:${y}px;width:${nodeWidth}px;height:${nodeHeight}px"><strong>${index + 1}. ${h(node.label)}</strong><small>${h({ waiting: "待处理", running: "进行中", succeeded: "已完成", failed: "失败", interrupted: "已中断" }[status(node.id)] ?? status(node.id))}</small></button>`;
    })
    .join(
      "",
    )}</div>${feedback.length ? `<ul class="execution-feedback-notes">${feedback.map((edge) => `<li><span class="tag">${edge.active ? "当前返回" : "历史返回"} × ${edge.count}</span> ${h(flow.nodes.find((node) => node.id === edge.source)?.label)} 返回 ${h(flow.nodes.find((node) => node.id === edge.target)?.label)}${edge.round ? " · 第 " + edge.round + " 轮" : ""}<p>${h(edge.reason)}</p></li>`).join("")}</ul>` : ""}`;
}
