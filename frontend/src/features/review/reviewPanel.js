const checks = { quality: "规范与质量", logic: "需求与逻辑", dependencies: "安全与依赖" };
const states = {
  checking: "等待必需报告",
  retryable: "检查可重试",
  changes_requested: "退回开发",
  awaiting_human: "待人工审核",
  approved: "人工批准",
  rejected: "人工拒绝",
  needs_attention: "待异常接管",
  stopped: "已终止",
};
const outcomes = { pending: "待报告", transient_error: "临时失败", failed: "检查失败", completed: "报告已收集" };

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text != null) element.textContent = text;
  if (className) element.className = className;
  return element;
}

export function mountReview(root) {
  let key;
  let context;
  let data;
  let busy;
  let error;

  function button(label, action, parent = root) {
    const element = node("button", label);
    element.type = "button";
    element.disabled = busy;
    element.addEventListener("click", action);
    parent.append(element);
    return element;
  }

  async function run(call, loading = false) {
    if (busy) return;
    const request = context;
    busy = true;
    error = "";
    if (loading) draw();
    else root.querySelectorAll("button").forEach((item) => { item.disabled = true; });
    try {
      const result = await call(request);
      if (request === context) data = result;
    } catch (err) {
      if (request === context) {
        error = err.message;
        // A stale decision must never leave a stale green state on screen.
        let refreshed = null;
        try { refreshed = await request.api.getReview(request.id, request.revision); }
        catch { /* Keep any unverified result off the screen. */ }
        if (request === context) data = refreshed;
      }
    } finally {
      if (request === context) {
        busy = false;
        draw();
      }
    }
  }

  function draw() {
    root.replaceChildren(node("h2", "审查中心 · AI_Review 与人工 Review"));
    root.append(node("p", "AI_Review 审查你提交的代码差异，人工 Review 决定是否放行。不会自动读取 GitHub、执行测试或合并 PR。", "review-note"));
    if (!context) {
      root.append(node("p", "请使用后端工作台并确认当前执行计划。", "review-note"));
      return;
    }
    const refresh = () => run(({ api, id, revision }) => api.getReview(id, revision), true);
    if (error) {
      const alert = node("p", error, "ws-message error");
      alert.setAttribute("role", "alert");
      root.append(alert);
    }
    button("刷新审查状态", refresh);
    if (!data) {
      if (busy) root.append(node("p", "正在加载审查状态…", "review-note"));
      return;
    }
    const round = data.current;
    const flow = node("ol", null, "review-flow");
    flow.setAttribute("aria-label", "执行编排的审查路径");
    const highlight = !round ? 0 : round.status === "approved" && !data.stale ? 4
      : ["awaiting_human", "rejected"].includes(round.status) || (round.status === "changes_requested" && round.blocked_by === "human") ? 3
      : round.automatic_decision ? 2 : 1;
    for (const [index, label] of ["开发提交", "AI_Review", "规则门禁", "人工 Review", "QA / 交付"].entries()) {
      const step = node("li", label);
      if (index === highlight && !data.stale) step.setAttribute("aria-current", "step");
      flow.append(step);
    }
    root.append(flow);
    const status = node("p", data.stale ? "历史修订 · 只读" : round ? states[round.status] : "等待开发提交", "review-status");
    status.setAttribute("role", "status");
    root.append(status, node("p", data.downstream_ready ? "下游 QA / 交付：已解除审核阻塞" : "下游 QA / 交付：仍阻塞", "review-note"));
    const controls = node("div", null, "ws-actions");
    root.append(controls);

    const mutate = (action, body) => run(({ api, id, revision }) => api.updateReview(id, action, {
      revision, commit_sha: round?.commit_sha, ...body,
    }));
    if (!data.stale && round?.mode !== "live" && (!round || ["checking", "retryable", "awaiting_human", "approved", "changes_requested"].includes(round.status))) {
      button(round ? "提交下一版（演示）" : "提交开发产物（演示）", () => {
        const hash = [...crypto.getRandomValues(new Uint8Array(20))]
          .map((b) => b.toString(16).padStart(2, "0")).join("");
        mutate("", {
          commit_sha: hash, previous_sha: round?.commit_sha,
          author: round?.author ?? "demo-author", change_ref: round?.change_ref ?? "demo-pr",
        });
      }, controls);
    }
    if (!data.stale && (!round || ["checking","awaiting_human","approved","changes_requested","retryable"].includes(round.status))) {
      const form = node("details", null, "review-submission");
      form.append(node("summary", round ? "登记下一版代码差异" : "登记代码差异 · 实时 AI_Review"));
      const fields = {};
      for (const [key, title, limit, value] of [
        ["commit_sha","Git 提交 SHA（40 位）",40,""], ["author","提交作者",100,round?.author ?? ""],
        ["change_ref","变更引用",100,round?.change_ref ?? ""], ["diff","代码差异（从 Git 复制，请先去除密钥）",28000,""],
      ]) {
        const label=node("label",title);
        const field=node(key==="diff"?"textarea":"input");
        field.maxLength=limit; field.value=value;
        if(key==="diff")field.rows=5;
        fields[key]=field;label.append(field);form.append(label);
      }
      const submit=button("登记提交",()=>mutate("", {
        ...Object.fromEntries(Object.entries(fields).map(([k,f])=>[k,k==="diff"?f.value:f.value.trim()])),
        mode:"live", previous_sha:round?.commit_sha,
      }),form);
      const validate=()=>{submit.disabled=busy || !/^[0-9a-f]{40}$/.test(fields.commit_sha.value.trim()) ||
        !fields.author.value.trim() || !fields.change_ref.value.trim() || !fields.diff.value.trim();};
      for(const field of Object.values(fields))field.addEventListener("input",validate);
      validate();
      root.append(form);
    }
    if (!round) return;
    root.append(node("p", `第 ${round.round} 轮 · 提交 ${round.commit_sha.slice(0, 12)} · 计划 r${data.revision} · 已返工 ${data.rework_count} 次`, "review-note"));
    root.append(node("h3", round.mode === "live" ? "01 · DeepSeek AI_Review" : "01 · AI_Review（模拟报告）"));
    if (!data.stale && round.mode === "live" && round.status === "checking") {
      button("运行 DeepSeek AI_Review", () => mutate("ai", {}), controls);
    }
    if (round.diff_digest) root.append(node("p", `差异摘要 SHA-256：${round.diff_digest}`, "review-note"));
    const grid = node("div", null, "review-checks");
    root.append(grid);
    const reportBody = (check, attempt, fields = {}) => ({
      revision: data.revision, commit_sha: round.commit_sha, check, attempt,
      outcome: "completed", ...fields,
    });
    for (const [check, label] of Object.entries(checks)) {
      const attempts = round.checks[check];
      const latest = attempts.at(-1);
      const card = node("section", null, "review-check");
      card.append(node("h3", label), node("p", `${outcomes[latest.outcome]} · 尝试 ${latest.attempt}`));
      if (latest.note) card.append(node("p", latest.note));
      for (const finding of latest.findings ?? []) {
        card.append(node("p", `${finding.severity}：${finding.message}`));
        if (finding.evidence) card.append(node("p", finding.evidence, "review-note"));
      }
      if (!data.stale && round.status === "retryable" && latest.outcome === "transient_error") {
        button("重试该检查", () => mutate("retry", { check, attempt: latest.attempt }), card);
      }
      if (attempts.length > 1) {
        const history = node("details");
        history.append(node("summary", "查看尝试记录"));
        for (const attempt of attempts) {
          history.append(node("p", `#${attempt.attempt} · ${outcomes[attempt.outcome]}${attempt.note ? ` · ${attempt.note}` : ""}`));
        }
        card.append(history);
      }
      grid.append(card);
    }
    if (!data.stale && round.mode !== "live" && ["checking", "retryable"].includes(round.status)) {
      const pending = Object.entries(round.checks).filter(([, attempts]) => attempts.at(-1).outcome === "pending");
      if (pending.length) {
        const reportPending = ({ api, id }, logicFinding) => Promise.all(pending.map(([check, attempts]) => {
          const finding = logicFinding && check === "logic" ? {
            findings: [{ severity: "high", message: "演示：验收条件缺少测试证据", evidence: "同一提交须补齐对应验收测试" }],
          } : {};
          return api.updateReview(id, "reports", reportBody(check, attempts.at(-1).attempt, finding));
        })).then(() => api.getReview(id, data.revision));
        button("提交通过报告（演示）", () => run((request) => reportPending(request, false)), controls);
        if (pending.some(([check]) => check === "logic")) {
          button("提交阻断报告（演示）", () => run((request) => reportPending(request, true)), controls);
        }
        const [check, attempts] = pending[0];
        button("模拟检查超时", () => mutate("reports", reportBody(check, attempts.at(-1).attempt, {
          outcome: "transient_error", note: "演示：检查暂时超时，可以安全重跑",
        })), controls);
      }
    }
    root.append(node("h3", "02 · 人工 Review"));
    if (round.status !== "awaiting_human" && !round.decision)
      root.append(node("p", "等待 AI_Review 门禁通过后，人工审核才可提交决定。", "review-note"));
    if (!data.stale && round.status === "awaiting_human") {
      const box = node("div", null, "review-decision");
      const who = node("label", "评审人（本地填写身份）");
      const reviewer = node("input");
      reviewer.maxLength=100;
      reviewer.value=round.mode==="live"?"":"demo-reviewer";
      who.append(reviewer);
      const label = node("label", "人工评审意见");
      const reason = node("textarea");
      reason.rows = 2;
      reason.maxLength = 2000;
      reason.placeholder = "说明批准依据、修改请求或拒绝原因";
      label.append(reason);
      const actions = node("div", null, "ws-actions");
      for (const [decision, title] of [["approve", "批准当前提交"], ["request_changes", "要求修改"], ["reject", "拒绝"]]) {
        const action = button(title, () => mutate("decision", {
          reviewer: reviewer.value.trim(), decision, reason: reason.value.trim(),
        }), actions);
        action.disabled = true;
        const validate=()=>{ action.disabled = busy || !reason.value.trim() || !reviewer.value.trim(); };
        reason.addEventListener("input",validate);
        reviewer.addEventListener("input",validate);
      }
      box.append(who, label, actions);
      root.append(box);
    }
    if (!data.stale && round.status === "needs_attention") {
      root.append(node("p", "负责人可以终止当前审查，或回到开发任务保存并重新确认新计划。", "review-note"));
      button("终止当前审查", () => mutate("stop", {
        operator: "demo-operator", reason: "演示：由负责人接管并终止当前审查",
      }), controls);
    }
    if (round.decision) root.append(node("p", `${round.decision.reviewer} · ${round.decision.reason}`));
    if (data.history.length) {
      const history = node("details");
      history.append(node("summary", `历史审查（${data.history.length} 轮）`));
      for (const old of [...data.history].reverse()) {
        history.append(node("p", `第 ${old.round} 轮 · ${old.commit_sha.slice(0, 12)} · ${states[old.status]} · ${old.decision?.reason ?? "无人工结论"}`));
      }
      root.append(history);
    }
  }

  return {
    update(api, plan) {
      const next = api.mode === "http" && plan ? `${api.base}|${plan.plan_id}|${plan.revision}` : null;
      if (key === next) return;
      key = next;
      data = null;
      error = "";
      busy = false;
      context = next ? { api, id: plan.plan_id, revision: plan.revision } : null;
      if (context) run(({ id, revision }) => api.getReview(id, revision), true);
      else draw();
    },
  };
}
