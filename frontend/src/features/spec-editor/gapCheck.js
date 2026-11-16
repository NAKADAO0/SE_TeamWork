/**
 * 规格缺口检查（界面层）。
 *
 * 只做“输入是否够用”的判断，输出可直接定位到字段的问题，供规格编辑器显示。
 * 权威校验在成员 3 的 backend/app/validation；这里的结果不写进 ValidationReport。
 */

const ID_PATTERN = /^[A-Za-z0-9_-]+$/;

export function gapCheck(draft) {
  const errors = [];
  const warnings = [];

  const error = (code, fieldPath, text, related = []) =>
    errors.push({ severity: "error", code, field_path: fieldPath, message: text, related_ids: related });
  const warn = (code, fieldPath, text, related = []) =>
    warnings.push({ severity: "warning", code, field_path: fieldPath, message: text, related_ids: related });

  if (!draft || typeof draft !== "object" || Array.isArray(draft)) {
    error("INVALID_SPEC", "", "规格必须是 JSON 对象");
    return { errors, warnings };
  }

  const product = draft.product ?? {};
  if (!String(product.name ?? "").trim()) {
    error("MISSING_PRODUCT", "product.name", "缺少产品名称");
  }
  if (String(product.scope ?? "").trim().length < 10) {
    error("MISSING_SCOPE", "product.scope", "产品范围过短，无法确定首期边界");
  }
  if (!Array.isArray(product.excluded) || product.excluded.length === 0) {
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

  if (!draft.constraints || Object.keys(draft.constraints).length === 0) {
    warn("MISSING_CONSTRAINTS", "constraints", "没有记录技术栈、平台或已有基础设施");
  }

  return { errors, warnings };
}
