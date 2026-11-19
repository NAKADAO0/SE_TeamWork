/** Split the displayed editors while keeping one complete document for existing APIs. */
export function splitSpecText(text = "") {
  const start = text.search(/^##\s+需求条目\s*$/m);
  if (start >= 0) return { scope: text.slice(0, start).trim(), remaining: text.slice(start).trim() };
  if (/^##\s+产品范围\s*$/m.test(text)) return { scope: text.trim(), remaining: "" };
  return { scope: "", remaining: text };
}

export function joinSpecText(scope, remaining) {
  return [scope.trim(), remaining.trim()].filter(Boolean).join("\n\n");
}
