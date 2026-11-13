import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setup, settle } from './helpers/openfilmHarness.mjs';

const scope = { session: 'session 1', persona: 'profile & 1' };
const pricing = kind => ({ session_id: 'session 1', profile_id: 'profile & 1', engine: 'hypit', kind, units: 1, unit_cost: kind === 'full' ? 4 : 1, estimated_cost: kind === 'full' ? 4 : 1, pricing_id: `hypit-${kind}-${kind === 'full' ? 4 : 1}`, valid_until: Date.now() + 600000 });

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

