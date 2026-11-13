import assert from 'node:assert/strict';
import { test } from 'node:test';
import { nodes, setup, settle } from './helpers/openfilmHarness.mjs';

const scope = { session: 'session 1', persona: 'profile & 1' };
const pricing = kind => ({ session_id: 'session 1', profile_id: 'profile & 1', engine: 'hypit', kind, units: 1, unit_cost: kind === 'full' ? 4 : 1, estimated_cost: kind === 'full' ? 4 : 1, pricing_id: `hypit-${kind}-${kind === 'full' ? 4 : 1}`, valid_until: Date.now() + 600000 });

const variant = { session_id: 'session 1', profile_id: 'profile & 1', variant_id: 'variant-1', approval_state: 'approved', selected_anchors: [], selected_assets: [], reference_evidence: [], analysis: {}, timeline: {} };

async function loadedWorkspace(readiness, pricingReply = pricing) {
  const view = setup('components/HypitReplicationWorkspace.tsx');
  view.render(scope);
  for (const request of view.pending) {
    if (request.url.pathname.endsWith('/readiness')) request.reply(readiness);
    else if (request.url.pathname.endsWith('/pricing')) request.reply(typeof pricingReply === 'function' ? pricingReply(request.url.searchParams.get('kind')) : pricingReply);
    else request.reply({ variants: [variant] });
  }
  await settle();
  await settle();
  return { view, tree: view.render(scope) };
}

test('workspace requires an account and session before it sends scoped requests', () => {
  const view = setup('components/HypitReplicationWorkspace.tsx');
  const tree = view.render({});
  assert.equal(view.pending.length, 0);
  assert.match(JSON.stringify(tree), /请选择账号并打开一个会话/);
  view.unmount();
});

test('workspace keeps Hypit requests scoped and renders malformed variant data safely', async () => {
  const view = setup('components/HypitReplicationWorkspace.tsx');
  view.render(scope);
  assert.equal(view.pending.length, 4);
  for (const request of view.pending) {
    assert.equal(request.url.searchParams.get('session_id'), scope.session);
    assert.equal(request.url.searchParams.get('profile_id'), scope.persona);
    if (request.url.pathname.endsWith('/readiness')) request.reply({ ready: true, engine: 'hypit' });
    else if (request.url.pathname.endsWith('/pricing')) request.reply(pricing(request.url.searchParams.get('kind')));
    else request.reply({ variants: 'malformed' });
  }
  await settle();
  await settle();
  view.render(scope);
  const tree = view.render(scope);
  assert.match(JSON.stringify(tree), /Hypit 复制工作台/);
  assert.match(JSON.stringify(tree), /暂无变体/);
  view.unmount();
});

test('replication action APIs preserve scope and explicit artifact selection', () => {
  const scope = { session_id: 's', profile_id: 'p' };
  for (const [exportName, args, expectedPath] of [
    ['startHypitRun', ['run 1', scope], '/api/hypit/runs/run%201/run'],
    ['approveHypitVariant', ['variant 1', scope], '/api/hypit/variants/variant%201/approve'],
    ['importHypitArtifact', ['run 1', scope, 'sample.mp4'], '/api/hypit/runs/run%201/artifacts/import'],
  ]) {
    const view = setup('lib/api.ts', exportName);
    view.render(args);
    const request = view.pending[0];
    assert.equal(request.url.pathname, expectedPath);
    const body = JSON.parse(request.options.body);
    assert.equal(body.session_id, scope.session_id);
    assert.equal(body.profile_id, scope.profile_id);
    if (exportName === 'importHypitArtifact') assert.equal(body.artifact, 'sample.mp4');
    view.unmount();
  }
});

test('provider route wording separates local, BYOK, and HypiHub billing', async () => {
  const local = await loadedWorkspace({ ready: true, engine: 'hypit', route: 'local' });
  const localText = JSON.stringify(local.tree);
  assert.match(localText, /本地构建 · 无平台费用/);
  assert.doesNotMatch(localText, /预计费用/);
  assert.doesNotMatch(localText, /units/);
  local.view.unmount();

  const byok = await loadedWorkspace({ ready: true, engine: 'hypit', provider: { route: 'byok' } });
  const byokText = JSON.stringify(byok.tree);
  assert.match(byokText, /使用自有 API Key，费用由 Provider 收取/);
  assert.doesNotMatch(byokText, /收据/);
  byok.view.unmount();

  const hypihub = await loadedWorkspace({ ready: true, engine: 'hypit', provider: { route: 'hypihub' }, approval: { approved: true, approval_id: 'approval-1' } }, kind => ({ ...pricing(kind), estimated_cost: kind === 'full' ? 8 : 2, currency: 'USD' }));
  const hypihubText = JSON.stringify(hypihub.tree);
  assert.match(hypihubText, /预计费用/);
  assert.match(hypihubText, /"2"/);
  assert.match(hypihubText, /USD/);
  assert.match(hypihubText, /收据/);
  assert.match(hypihubText, /已批准/);
  hypihub.view.unmount();
});

test('readiness-off disables Build and explains runtime requirements', async () => {
  const { view, tree } = await loadedWorkspace({ ready: false, engine: 'hypit', route: 'local', message: '请安装 Hypit runtime', runtime_requirements: ['ffmpeg', 'Hypit runtime'] });
  const text = JSON.stringify(tree);
  assert.match(text, /请安装 Hypit runtime/);
  assert.match(text, /ffmpeg/);
  const buildButton = nodes(tree, 'button').find(item => String(item.props.children).includes('开始 Sample Build'));
  assert.ok(buildButton);
  assert.equal(buildButton.props.disabled, true);
  view.unmount();
});

test('malformed HypiHub pricing disables Build without showing a fake receipt', async () => {
  const { view, tree } = await loadedWorkspace({ ready: true, engine: 'hypit', provider: { route: 'hypihub' } }, { pricing_id: 'broken', estimated_cost: 'free', currency: 'USD' });
  const text = JSON.stringify(tree);
  assert.match(text, /费用估算格式无效/);
  assert.doesNotMatch(text, /收据 broken/);
  const buildButton = nodes(tree, 'button').find(item => String(item.props.children).includes('开始 Sample Build'));
  assert.equal(buildButton.props.disabled, true);
  view.unmount();
});

test('failed Build output remains visible with its actionable error', async () => {
  const { view, tree } = await loadedWorkspace({ ready: true, engine: 'hypit', route: 'local' });
  const buildButton = nodes(tree, 'button').find(item => String(item.props.children).includes('开始 Sample Build'));
  assert.ok(buildButton);
  buildButton.props.onClick();
  await settle();
  const planRequest = view.pending.at(-1);
  assert.equal(planRequest.url.pathname, '/api/hypit/runs');
  planRequest.reply({ id: 'run-1', run_id: 'run-1', session_id: scope.session, profile_id: scope.persona, state: 'planned' });
  await settle();
  const startRequest = view.pending.at(-1);
  assert.match(startRequest.url.pathname, /\/api\/hypit\/runs\/run-1\/run$/);
  startRequest.reply({ id: 'run-1', run_id: 'run-1', session_id: scope.session, profile_id: scope.persona, state: 'running' });
  await settle();
  const statusRequest = view.pending.at(-1);
  assert.match(statusRequest.url.pathname, /\/api\/hypit\/runs\/run-1$/);
  statusRequest.reply({ id: 'run-1', run_id: 'run-1', session_id: scope.session, profile_id: scope.persona, state: 'failed', error: 'runner failed: output validation' });
  await settle();
  for (const request of view.pending.slice(-3)) {
    if (request.url.pathname.endsWith('/logs')) request.reply({ logs: ['output validation failed'] });
    else if (request.url.pathname.endsWith('/reports')) request.reply({ reports: [] });
    else if (request.url.pathname.endsWith('/artifacts')) request.reply({ artifacts: [] });
  }
  await settle();
  const failedTree = view.render(scope);
  assert.match(JSON.stringify(failedTree), /失败/);
  assert.match(JSON.stringify(failedTree), /runner failed: output validation/);
  view.unmount();
});

