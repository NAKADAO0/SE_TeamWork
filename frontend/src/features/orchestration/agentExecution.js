import { escapeHtml as safe } from "../../shared/dom.js";

const statusText = { running: "正在派发", paused: "已暂停 · 等待人工继续", failed: "执行失败 · 可重试", done: "批次已完成" };

export function mountAgentExecution(root, onChange) {
  root.innerHTML = `
    <p data-agent-scope>Lab1 按此修订的批次及依赖派发 Agent，生成待复核草稿。</p>
    <p data-agent-state class="orch-run-status" role="status"></p>
    <p data-agent-error class="ws-message error" role="alert"></p>
    <div data-agent-start class="ws-actions">
      <button type="button" data-run="demo">启动规则演示</button>
      <button type="button" data-run="live">启动 DeepSeek 执行</button>
    </div>
    <form data-agent-note hidden>
      <label for="agent-note">人工讨论意见</label>
      <textarea id="agent-note" rows="3" maxlength="3000" required placeholder="例如：核对取消后立即释放时段的验收条件"></textarea>
      <div class="ws-actions">
        <button type="submit" data-interrupt>打断并加入讨论</button>
        <button type="button" data-resume hidden>继续当前批次</button>
      </div>
    </form>
    <div data-agent-active></div>
    <div data-agent-artifacts></div>
    <div data-agent-history></div>`;
  const el = (selector) => root.querySelector(selector);
  const start = [...root.querySelectorAll("[data-run]")];
  const form = el("[data-agent-note]");
  const input = el("#agent-note");
  const resume = el("[data-resume]");
  const keyOf = (api, plan) => `se.agent-run:${api.base}:${plan.plan_id}:r${plan.revision}`;
  let context;
  let run;
  let loading = false;
  let serial = 0;
  let error = "";
  let timer;
  let resultKey = "";
  let scope;
  let initialRun = new URLSearchParams(location.search).get("agent_run");

  function remember(id) {
    try { id ? localStorage.setItem(context.key, id) : localStorage.removeItem(context.key); } catch {}
  }
  function render() {
    const total = run?.execution?.batches.length ?? 0;
    const batch = run?.execution?.batches[run.cursor] ?? [];
    const current = scope ? batch.filter((id) => scope.task_ids.includes(id)) : batch;
    const mode = run?.mode === "live" ? "DeepSeek 实时" : "规则演示";
    el("[data-agent-state]").textContent = run
      ? `${mode} · ${statusText[run.status] ?? run.status} · ${run.cursor} / ${total} 批已完成`
      : context ? "确认版已就绪，可以派发 Agent。" : "请先在「执行编排」确认当前计划。";
    el("[data-agent-error]").textContent = error || run?.error || "";
    el("[data-agent-scope]").textContent = scope
      ? "此处显示当前工作包的 Agent 产物和对话。从这里加入讨论会暂停项目当前批次，意见带工作包编号，恢复后交给 Agent。"
      : "Lab1 按此修订的批次及依赖派发 Agent，生成待复核草稿。";
    el("[data-agent-start]").hidden = Boolean(run && run.status !== "done");
    for (const button of start) button.disabled = !context || loading;
    form.hidden = !run || run.status === "done";
    input.disabled = loading;
    input.maxLength = 3000 - notePrefix().length;
    el("[data-interrupt]").disabled = loading;
    el("[data-interrupt]").textContent = run?.status === "paused" ? "追加讨论意见" : "打断并加入讨论";
    resume.hidden = !["paused", "failed"].includes(run?.status);
    resume.disabled = loading;
    el("[data-agent-active]").innerHTML = current.length ? `
      <h3>当前批次 · ${run.cursor + 1}</h3><ul class="orch-agent-tasks">${current.map((id) => {
        const node = run.execution.nodes[id];
        const preview = run.previews?.find((message) => message.task_id === id);
        return `<li><strong>${safe(node.task.title)}</strong><small>${safe(id)} · ${safe(node.agent)}</small>
          ${preview?.text ? `<p>${safe(preview.text)}</p>` : ""}</li>`;
      }).join("")}</ul>` : "";
    const key = run ? `${run.id}:${run.cursor}:${scope?.id ?? ""}` : "";
    if (key !== resultKey) {
      resultKey = key;
      const results = Object.entries(run?.results ?? {}).filter(([id]) => !scope || scope.task_ids.includes(id));
      const output = el("[data-agent-artifacts]");
      const expanded = new Set([...output.querySelectorAll("details[open]")].map((item) => item.dataset.artifact));
      output.innerHTML = results.length ? `<h3>任务草稿 · ${results.length}</h3>${results.map(([id, value]) =>
        `<details data-artifact="${safe(id)}" ${expanded.has(id) ? "open" : ""}><summary>${safe(id)} · ${safe(run.execution.nodes[id]?.task?.title)}</summary>
          <p>${safe(value.summary)}</p><pre>${safe(value.artifact)}</pre></details>`).join("")}` : "";
    }
    const messages = (run?.messages ?? []).filter((message) => {
      if (message.actor === "system") return true;
      if (message.actor === "human") return !scope || !message.text.startsWith("【工作包 ") || message.text.startsWith(`【工作包 ${scope.id}：`);
      return Boolean(scope && scope.task_ids.includes(message.task_id));
    }).slice(-12);
    el("[data-agent-history]").innerHTML = messages.length ? `<h3>讨论与运行记录</h3><ol class="orch-agent-log">${messages.map((message) =>
      `<li><strong>${message.actor === "human" ? "人工" : message.actor === "system" ? "系统" : `Agent · ${safe(message.task_id)}`}</strong><span>${safe(message.text)}</span></li>`).join("")}</ol>` : "";
  }

  const notePrefix = () => scope ? `【工作包 ${scope.id}：${scope.title}】` : "";

  function publish() { render(); onChange(run, { loading, error }); }

  async function request(load, { interactive = false, restoring = false } = {}) {
    const target = context;
    if (!target || loading) return false;
    clearTimeout(timer);
    const sequence = ++serial;
    loading = interactive;
    if (interactive) {
      error = "";
      publish();
    }
    let success = false;
    try {
      const next = await load(target);
      if (target !== context || sequence !== serial) return false;
      run = next;
      remember(run.id);
      error = "";
      success = true;
    } catch (cause) {
      if (target !== context || sequence !== serial) return false;
      if (restoring && cause.code === "HTTP_404") remember(null);
      error = cause.message;
    } finally {
      if (target === context && sequence === serial) {
        loading = false;
        publish();
        if (run?.status === "running") {
          timer = setTimeout(() => request(({ api, plan }) =>
            api.getAgentRun(plan.plan_id, plan.revision, run.id)), success ? 650 : 2300);
        }
      }
    }
    return success;
  }

  for (const button of start) button.onclick = async () => {
    const started = await request(({ api, plan }) =>
      api.startAgentRun(plan.plan_id, plan.revision, button.dataset.run), { interactive: true });
    if (!started) return;
    const url = new URL(location.href);
    if (url.searchParams.has("agent_run")) {
      url.searchParams.set("agent_run", run.id);
      history.replaceState(history.state, "", url);
    }
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || !run) return;
    if (await request(({ api, plan }) =>
      api.updateAgentRun(plan.plan_id, plan.revision, run.id, "interrupt", { text: notePrefix() + text }), { interactive: true })) input.value = "";
  };
  resume.onclick = () => request(({ api, plan }) =>
    api.updateAgentRun(plan.plan_id, plan.revision, run.id, "resume", {}), { interactive: true });

  render();
  return {
    setScope(group) {
      if (scope?.id !== group?.id) input.value = "";
      scope = group;
      render();
    },
    update(api, plan) {
      const key = api.mode === "http" && plan ? keyOf(api, plan) : null;
      if (key === (context?.key ?? null)) return;
      context = key ? { key, api, plan } : null;
      clearTimeout(timer);
      run = null;
      loading = false;
      error = "";
      input.value = "";
      publish();
      if (!context) return;
      let id;
      try { id = localStorage.getItem(key); } catch {}
      if (initialRun) {
        id = initialRun;
        initialRun = null;
      }
      if (/^[a-f0-9]{14}$/.test(id ?? "")) request(({ api, plan }) =>
        api.getAgentRun(plan.plan_id, plan.revision, id), { restoring: true, interactive: true });
    },
  };
}
