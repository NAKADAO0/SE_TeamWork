// Adapt member 5's saved plans and execution snapshots for the graph view.
export async function loadRemoteGraph(params) {
  const configured = params.get('api');
  const defaultBase = location.port === '8010' || !location.port ? '' : 'http://127.0.0.1:8010';
  const base = (configured === 'http' ? defaultBase : configured).replace(/\/$/, '');
  async function read(path) {
    let response;
    try { response = await fetch(`${base}${path}`); }
    catch { throw new Error('无法连接计划服务，请确认后端已启动'); }
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `读取失败（${response.status}）`);
    return body;
  }
  const plans = await read('/plans');
  if (!plans.length) throw new Error('暂无已保存计划');
  const planId = params.get('plan_id') ?? plans[0].plan_id;
  const revisions = await read(`/plans/${encodeURIComponent(planId)}/revisions`);
  const requested = params.get('revision');
  const snapshot = await read(`/plans/${encodeURIComponent(planId)}/graph${requested ? `?revision=${encodeURIComponent(requested)}` : ''}`);
  return { plans, revisions, planId, plan: snapshot.plan };
}
