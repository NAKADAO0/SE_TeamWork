// Presentation metadata for curated examples only. No real execution is recorded.
export const sampleProgress = {
  chat: { phases: [
    { title: '设计与定义', tasks: ['C1'] },
    { title: '功能实现', tasks: ['C2', 'C3', 'C4', 'C5'] },
    { title: '验证与联调', tasks: ['C6', 'C7', 'C8'] },
  ] },
};
sampleProgress.community = {
  phases: [
    { title: '设计与定义', tasks: ['N1'] },
    { title: '功能实现', tasks: ['N2', 'N3', 'N4', 'N5'] },
    { title: '验证与联调', tasks: ['N6', 'N7', 'N8'] },
  ],
};

// Execution states are illustrative snapshots, separate from plan preparation status.
sampleProgress.chat.planStatus = 'compiled';
sampleProgress.chat.execution = {
  C1: { status: 'completed' }, C2: { status: 'completed' }, C3: { status: 'completed' },
  C4: { status: 'running' },
  C5: { status: 'pending', reason: '正常等待 C4 完成历史查询接口。' },
  C6: { status: 'failed', reason: '模拟：测试执行器启动失败，未能完成会话隔离验证。' },
  C7: { status: 'pending', reason: '正常等待 C4、C5 完成。' },
  C8: { status: 'blocked', reason: '模拟：前置任务 C6 失败，需要修复并重试；同时等待 C7。' },
};
sampleProgress.community.planStatus = 'compiled';
sampleProgress.community.execution = {
  N1: { status: 'completed' }, N2: { status: 'completed' },
  N3: { status: 'completed' }, N4: { status: 'completed' },
  N5: { status: 'running' },
  N6: { status: 'pending', reason: '正常等待 N5 完成内容流。' },
  N7: { status: 'pending', reason: '正常等待 N5 完成内容流。' },
  N8: { status: 'pending', reason: '正常等待 N6、N7 验证完成。' },
};

// Larger curated graph: wide parallel branches, joins and a global concurrency limit.
sampleProgress.complex = {
  planStatus: 'compiled',
  phases: [
    { title: '设计与定义', tasks: ['P01', 'P02', 'P03', 'P04'] },
    { title: '功能实现', tasks: ['P05', 'P06', 'P07', 'P08', 'P09', 'P10', 'P11', 'P12', 'P13', 'P14'] },
    { title: '专项验证', tasks: ['P15', 'P16', 'P17', 'P18', 'P19', 'P20'] },
    { title: '集成与交付', tasks: ['P21', 'P22', 'P23', 'P24'] },
  ],
  execution: Object.fromEntries(Array.from({ length: 24 }, (_, i) => {
    const id = `P${String(i + 1).padStart(2, '0')}`;
    if (i < 6) return [id, { status: 'completed' }];
    if (i === 6) return [id, { status: 'running' }];
    if (i === 7) return [id, { status: 'failed', reason: '模拟：支付测试环境不可用，订单回调适配未完成。' }];
    if ([17, 19, 20, 21, 22, 23].includes(i)) return [id, { status: 'blocked', reason: '模拟：支付任务 P08 失败，当前任务的前置链无法完成，需修复后继续。' }];
    return [id, { status: 'pending', reason: '正常等待前置任务完成或执行名额释放。' }];
  })),
};
