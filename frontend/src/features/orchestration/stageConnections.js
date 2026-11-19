const MAIN = [
  ["project", "spec-ai"], ["spec-ai", "spec-human"], ["spec-human", "tasks"],
  ["tasks", "flow"], ["flow", "agents"], ["agents", "review"], ["review", "human"],
].map(([from, to]) => ({ from, to, kind: "main" }));

const LINKS = [
  { from: "spec-human", to: "review", kind: "input", label: "代码 AI_Review 通过任务关联的需求编号和验收条件核对代码。" },
  { from: "tasks", to: "human", kind: "input", label: "开发任务与验收清单，供人工审核核对；仍需通过代码 AI_Review。" },
  { from: "flow", to: "tasks", kind: "feedback", label: "依赖或覆盖校验未通过时，修订任务后重新编排。" },
  { from: "review", to: "agents", kind: "feedback", label: "代码 AI_Review 要求修改时，退回开发，修改后重新提交差异。" },
  { from: "human", to: "agents", kind: "feedback", label: "人工审核要求修改时，退回开发，修改后重新提交差异。" },
];

function route({ from, to, kind }, a, b, width, bottom) {
  const pad = 4;
  if (kind === "input") {
    const dx = from === "tasks" ? -19 : 0;
    return `M ${a.cx + dx} ${a.bottom + pad} L ${b.cx} ${b.top - pad}`;
  }
  if (kind === "feedback" && from === "flow") {
    // Leave the main transition between these cards clear; the return takes the outer lane.
    const x = width - 4;
    return `M ${a.right + pad} ${a.cy} H ${x - 5} Q ${x} ${a.cy} ${x} ${a.cy - 5}
      V ${b.cy + 5} Q ${x} ${b.cy} ${x - 5} ${b.cy} H ${b.right + pad}`;
  }
  if (kind === "feedback") {
    const fromHuman = from === "human";
    const x = b.cx + (fromHuman ? 17 : -17);
    const y = bottom + (fromHuman ? 40 : 18);
    return `M ${a.cx} ${a.bottom + pad} V ${y - 6} Q ${a.cx} ${y} ${a.cx + 6} ${y}
      H ${x - 6} Q ${x} ${y} ${x} ${y - 6} V ${b.bottom + pad}`;
  }
  if (from === "tasks" && to === "flow") {
    return `M ${a.cx} ${a.bottom + pad} V ${b.top - pad}`;
  }
  return a.cx < b.cx
    ? `M ${a.right + pad} ${a.cy} L ${b.left - pad} ${b.cy}`
    : `M ${a.left - pad} ${a.cy} L ${b.right + pad} ${b.cy}`;
}

export function mountStageConnections(map, cards, steps, explain) {
  map.insertAdjacentHTML("beforeend", `<svg class="orch-stage-edges" aria-hidden="true">
    <defs>${["main", "input", "feedback"].map((kind) =>
      `<marker id="orch-tip-${kind}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" markerUnits="userSpaceOnUse" orient="auto">
         <path class="orch-tip-${kind}" d="M0 0 L10 5 L0 10z"/>
       </marker>`).join("")}</defs>
    ${[...MAIN, ...LINKS].map(({ from, to, kind }) =>
      `<path data-from="${from}" data-to="${to}" data-kind="${kind}" marker-end="url(#orch-tip-${kind})"/>`).join("")}
  </svg>`);
  const svg = map.querySelector("svg");
  const paths = [...svg.querySelectorAll("path[data-from]")];
  const title = Object.fromEntries(steps.map(([key, name, , index]) => [key, `${index} ${name}`]));
  explain.innerHTML = LINKS.map(({ from, to, kind, label }) =>
    `<li data-kind="${kind}"><span>${kind === "input" ? "复核依据" : "退回修改"}</span>
      <strong>${title[from]} → ${title[to]}</strong><p>${label}</p></li>`).join("");

  function draw() {
    const frame = map.getBoundingClientRect();
    if (!frame.width || !frame.height) return;
    const byKey = new Map(cards.map((card) => {
      const rect = card.getBoundingClientRect();
      const left = rect.left - frame.left, top = rect.top - frame.top;
      return [card.dataset.orchStep, {
        left, top, right: left + rect.width, bottom: top + rect.height,
        cx: left + rect.width / 2, cy: top + rect.height / 2,
      }];
    }));
    const bottom = Math.max(...[...byKey.values()].map((card) => card.bottom));
    svg.setAttribute("viewBox", `0 0 ${frame.width} ${frame.height}`);
    for (const path of paths) {
      const { from, to } = path.dataset;
      path.setAttribute("d", route(path.dataset, byKey.get(from), byKey.get(to), frame.width, bottom));
    }
  }

  const observer = new ResizeObserver(draw);
  observer.observe(map);
  cards.forEach((card) => observer.observe(card));
  draw();
  return {
    select(key) {
      for (const path of paths) {
        path.dataset.related = String(path.dataset.from === key || path.dataset.to === key);
      }
    },
  };
}
