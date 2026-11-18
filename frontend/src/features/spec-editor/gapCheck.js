/**
 * 规格缺口检查（界面层）。
 *
 * 只做“输入是否够用”的判断，输出可直接定位到字段的问题，供规格编辑器显示。
 * 兼容前端草稿 JSON（product.name）与契约 DesignSpec JSON（product 字符串 + scope 对象）。
 * 权威校验在后端 backend/app/contracts/spec_intake.py 与 backend/app/validation。
 */

const ID_PATTERN = /^[A-Za-z0-9_-]+$/;

export function parseSpecMarkdownClient(text) {
  const lines = String(text ?? "").split(/\r?\n/);
  let product = "";
  const sections = new Map();
  let current = "";
  for (const line of lines) {
    const heading = line.match(/^(#{1,6})\s+(.+?)\s*$/);
    if (heading) {
      const level = heading[1].length;
      const title = heading[2].replace(/`/g, "").trim();
      if (level === 1 && !product) {
        product = title.split(/\s*[·|｜]\s*/)[0].trim();
      } else {
        current = title;
        if (!sections.has(current)) sections.set(current, []);
      }
      continue;
    }
    if (current) sections.get(current).push(line);
  }
  if (!product && !sections.size) {
    return {
      product: String(text ?? "").trim(),
      scope: { goal: "", included: [], excluded: [] },
      requirements: [],
      modules: [],
      design_items: [],
      constraints: [],
    };
  }
  const findSection = (...keys) => {
    for (const [name, body] of sections.entries()) {
      if (keys.some((k) => name.toLowerCase().includes(k))) return body;
    }
    return [];
  };
  const parseTable = (body) => {
    const rows = [];
    for (const raw of body) {
      const s = raw.trim();
      if (!s.startsWith("|")) continue;
      const cells = s.replace(/^\|+|\|+$/g, "").split("|").map((c) => c.replace(/`/g, "").trim());
      if (cells.every((c) => !c || /^:?-{2,}:?$/.test(c.replace(/\s+/g, "")))) continue;
      rows.push(cells);
    }
    if (rows.length < 2) return [];
    const headers = rows[0];
    return rows.slice(1).map((cells) => {
      const obj = {};
      headers.forEach((h, i) => { obj[h] = cells[i] ?? ""; });
      return obj;
    });
  };

  const scopeLines = findSection("范围", "scope");
  const goalParts = [];
  const excluded = [];
  let inExcl = false;
  for (const raw of scopeLines) {
    const s = raw.trim();
    if (!s) continue;
    if (/排除|不做|不包含|excluded/i.test(s)) {
      inExcl = true;
      continue;
    }
    const bullet = s.match(/^(?:[-*+]|\d+\.)\s+(.+)$/);
    if (bullet && inExcl) excluded.push(bullet[1].replace(/`/g, "").trim());
    else if (!bullet) goalParts.push(s.replace(/`/g, "").trim());
  }

  const reqLines = findSection("需求", "requirement");
  const requirements = parseTable(reqLines)
    .map((r) => ({
      id: r["编号"] || r["ID"] || r["id"] || "",
      goal: r["行为描述"] || r["描述"] || r["目标"] || r["goal"] || "",
      acceptance: r["验收条件"] || r["验收"] || r["acceptance"] || "",
    }))
    .filter((r) => r.id || r.goal);

  const allReqIds = requirements.map((r) => r.id).filter(Boolean);
  const modLines = findSection("模块", "module");
  const modules = parseTable(modLines)
    .map((m) => {
      const id = m["编号"] || m["ID"] || m["id"] || "";
      const rel = m["关联需求"] || m["requirement_ids"] || "";
      const reqIds = /全部|all/i.test(rel)
        ? [...allReqIds]
        : rel.split(/[、,，;；\s]+/).map((x) => x.trim()).filter((x) => ID_PATTERN.test(x));
      return {
        id,
        responsibility: [m["名称"], m["职责"] || m["responsibility"]].filter(Boolean).join("："),
        requirement_ids: reqIds,
      };
    })
    .filter((m) => m.id);

  return {
    product,
    scope: { goal: goalParts.join(" "), included: [], excluded },
    requirements,
    modules,
    design_items: [],
    constraints: [],
  };
}

export function gapCheck(draft) {
  const errors = [];
  const warnings = [];

  const error = (code, fieldPath, text, related = []) =>
    errors.push({ severity: "error", code, field_path: fieldPath, message: text, related_ids: related });
  const warn = (code, fieldPath, text, related = []) =>
    warnings.push({ severity: "warning", code, field_path: fieldPath, message: text, related_ids: related });

  if (typeof draft === "string") {
    draft = parseSpecMarkdownClient(draft);
  }

  if (!draft || typeof draft !== "object" || Array.isArray(draft)) {
    error("INVALID_SPEC", "", "规格必须是 JSON 对象或 Markdown 设计文档");
    return { errors, warnings };
  }

  const productName = typeof draft.product === "string" ? draft.product : draft.product?.name ?? "";
  const scopeGoal = typeof draft.scope === "string"
    ? draft.scope
    : draft.scope?.goal ?? draft.product?.scope ?? "";
  const scopeIncluded = draft.scope?.included ?? draft.scope?.include ?? [];
  const scopeExcluded = draft.scope?.excluded ?? draft.scope?.exclude ?? draft.product?.excluded ?? [];

  if (!String(productName).trim()) {
    error("MISSING_PRODUCT", "product.name", "缺少产品名称");
  }
  if (String(scopeGoal).trim().length < 8 && (!Array.isArray(scopeIncluded) || scopeIncluded.length === 0)) {
    error("MISSING_SCOPE", "product.scope", "产品范围过短，无法确定首期边界");
  }
  if (!Array.isArray(scopeExcluded) || scopeExcluded.length === 0) {
    warn("MISSING_EXCLUSION", "product.excluded", "没有写明明确排除项，范围容易被放大");
  }

  const requirements = draft.requirements;
  if (!Array.isArray(requirements) || requirements.length === 0) {
    error("MISSING_REQUIREMENTS", "requirements", "至少需要一条需求");
    return { errors, warnings };
  }

  const seen = new Map();
  requirements.forEach((requirement, index) => {
    const path = `requirements[${index}]`;
    const id = String(requirement?.id ?? "");
    const related = id ? [id] : [];
    if (!ID_PATTERN.test(id)) {
      error("INVALID_REQUIREMENT_ID", `${path}.id`, `需求编号 ${id || "(空)"} 只能包含字母、数字、下划线和连字符`, related);
      return;
    }
    if (seen.has(id)) {
      error("DUPLICATE_REQUIREMENT_ID", `${path}.id`, `需求编号 ${id} 重复`, related);
    }
    seen.set(id, true);
    if (!String(requirement?.goal ?? "").trim()) {
      error("MISSING_REQUIREMENT_GOAL", `${path}.goal`, `需求 ${id} 缺少行为描述`, related);
    }
    const acceptance = Array.isArray(requirement?.acceptance)
      ? requirement.acceptance
      : [requirement?.acceptance ?? ""];
    if (!acceptance.some((item) => String(item).trim())) {
      error("MISSING_REQUIREMENT_ACCEPTANCE", `${path}.acceptance`, `需求 ${id} 缺少验收条件`, related);
    } else if (acceptance.length > 3) {
      warn("MANY_ACCEPTANCE_LINES", `${path}.acceptance`, `需求 ${id} 的验收条件较多，考虑拆分需求`, related);
    }
  });

  const modules = draft.modules ?? [];
  if (!Array.isArray(modules) || modules.length === 0) {
    warn("MISSING_MODULES", "modules", "没有模块划分，任务分解会缺少归属依据");
  } else {
    modules.forEach((module, index) => {
      const path = `modules[${index}]`;
      if (!ID_PATTERN.test(String(module?.id ?? ""))) {
        error("INVALID_MODULE_ID", `${path}.id`, "模块编号不合法");
      }
      if (!String(module?.responsibility ?? "").trim()) {
        error("MISSING_MODULE_RESPONSIBILITY", `${path}.responsibility`, `模块 ${module?.id ?? index} 缺少职责说明`);
      }
      for (const requirementId of module?.requirement_ids ?? []) {
        if (!seen.has(requirementId)) {
          error(
            "UNKNOWN_MODULE_REQUIREMENT",
            `${path}.requirement_ids`,
            `模块 ${module?.id ?? index} 引用了不存在的需求 ${requirementId}`,
            [requirementId],
          );
        }
      }
    });
  }

  const hasConstraints = Array.isArray(draft.constraints)
    ? draft.constraints.length > 0
    : Boolean(draft.constraints && Object.keys(draft.constraints).length > 0);
  if (!hasConstraints) {
    warn("MISSING_CONSTRAINTS", "constraints", "没有记录技术栈、平台或已有基础设施");
  }

  return { errors, warnings };
}
