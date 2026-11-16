export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

export function listText(value) {
  if (Array.isArray(value)) return value.join("；");
  return String(value ?? "");
}

export function download(filename, text, type = "application/json") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function modeLabel(mode) {
  return (
    {
      live_llm: "实时模型",
      offline_rule: "离线规则",
      fixture_replay: "样例回放",
      curated_example: "预编排案例",
    }[mode] ?? mode ?? "未标注"
  );
}
