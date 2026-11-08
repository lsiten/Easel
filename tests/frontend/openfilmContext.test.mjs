import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setup, nodes, info, settle } from './helpers/openfilmHarness.mjs';

test('slow Studio requests settle before the next poll begins', async () => {
  const view = setup('lib/useOpenfilmStudio.ts', 'useOpenfilmStudio');
  const props = [{ session: 'slow', persona: '甲' }, 5000];
  view.render(props);
  for (const tick of view.timers.values()) tick();
  assert.equal(view.pending.length, 1);
  view.pending[0].reply(info('own'));
  await settle();
  assert.equal(view.render(props).studio.studio, 'https://studio.test/own');
  assert.equal(view.timers.size, 1);
  for (const tick of view.timers.values()) tick();
  assert.equal(view.pending.length, 2);
  assert.equal(view.timers.size, 0);
  view.unmount();
  assert.equal(view.pending[1].options.signal.aborted, true);
});

test('matching preview consumers share the conversation poll and manual refresh', async () => {
  const owner = setup('lib/useOpenfilmStudio.ts', 'useOpenfilmStudio');
  const props = [{ session: 'chat', persona: '甲' }, 6000];
  owner.render(props);
  owner.pending[0].reply(info('own'));
  await settle();
  owner.provideStudio(owner.render(props));
  const child = owner.mount('lib/useOpenfilmStudio.ts', 'useOpenfilmStudio');
  assert.equal(child.render(props).studio.studio, 'https://studio.test/own');
  assert.equal(owner.pending.length, 1);
  child.render(props).refresh();
  owner.provideStudio(null);
  owner.render(props);
  assert.equal(owner.pending.length, 2);
  owner.pending[1].reply(info('updated'));
  await settle();
  owner.provideStudio(owner.render(props));
  assert.equal(child.render(props).studio.studio, 'https://studio.test/updated');
  child.unmount();
  owner.unmount();
  assert.equal(owner.timers.size, 0);
});

test('disabled conversation polling sends no request and drops previous state', async () => {
  const view = setup('lib/useOpenfilmStudio.ts', 'useOpenfilmStudio');
  const context = { session: 'chat', persona: '甲' };
  view.render([context, 6000]);
  view.pending[0].reply(info('own'));
  await settle();
  const state = view.render([{ ...context, enabled: false }, 6000]);
  assert.equal(state.studio, null);
  assert.equal(view.pending.length, 1);
  assert.equal(view.timers.size, 0);
  view.unmount();
});

test('another persona cannot inherit the conversation provider state', async () => {
  const owner = setup('lib/useOpenfilmStudio.ts', 'useOpenfilmStudio');
  owner.render([{ session: 'chat', persona: '甲' }]);
  owner.pending[0].reply(info('first-persona'));
  await settle();
  owner.provideStudio(owner.render([{ session: 'chat', persona: '甲' }]));
  const child = owner.mount('lib/useOpenfilmStudio.ts', 'useOpenfilmStudio');
  const props = [{ session: 'chat', persona: '乙' }];
  assert.equal(child.render(props).studio, null);
  owner.pending[1].reply(info('second-persona'));
  await settle();
  assert.equal(child.render(props).studio.studio, 'https://studio.test/second-persona');
  child.unmount();
  owner.unmount();
});

for (const changed of [{ session: 'b', persona: '甲' }, { session: 'a', persona: '乙' }]) {
  test(`Studio hides previous project and rejects delayed responses after switching to ${JSON.stringify(changed)}`, async () => {
    const view = setup('components/VideoStudioPanel.tsx');
    const original = { session: 'a', persona: '甲' };
    view.render(original);
    view.pending[0].reply(info('old'));
    await settle();
    assert.equal(nodes(view.render(original), 'iframe')[0].props.src, 'https://studio.test/old');
    for (const callback of view.timers.values()) callback();
    const late = view.pending.at(-1);
    assert.equal(nodes(view.render(changed), 'iframe').length, 0);
    view.pending.at(-1).reply(info('new'));
    await settle();
    late.reply(info('late-old'));
    await settle();
    assert.equal(nodes(view.render(changed), 'iframe')[0].props.src, 'https://studio.test/new');
    assert.equal(nodes(view.render(original, false), 'iframe').length, 0);
    view.unmount();
    assert.equal(view.timers.size, 0);
  });
}

test('Studio accepts references only from its displayed iframe and origin', async () => {
  const view = setup('components/VideoStudioPanel.tsx');
  const refs = [];
  const props = { session: 'a', persona: '甲', onAddRef: ref => refs.push(ref) };
  view.render(props);
  view.pending[0].reply(info('project'));
  await settle();
  const iframe = nodes(view.render(props), 'iframe')[0];
  const source = {};
  if (iframe.props.ref) iframe.props.ref.current = { contentWindow: source };
  const data = { source: 'openfilm-studio', type: 'openfilm:reference', ref: { kind: 'file', path: 'own.png' } };
  for (const listener of view.listeners) {
    listener({ data, source: {}, origin: 'https://studio.test' });
    listener({ data, source, origin: 'https://other.test' });
    listener({ data: { ...data, source: 'other' }, source, origin: 'https://studio.test' });
    listener({ data: { ...data, type: 'other' }, source, origin: 'https://studio.test' });
  }
  assert.equal(refs.length, 0);
  for (const listener of view.listeners) listener({ data, source, origin: 'https://studio.test' });
  assert.equal(refs.length, 1);
  view.unmount();
});

for (const changed of [{ session: 'b', persona: '甲' }, { session: 'a', persona: '乙' }]) {
  test(`pending references are cleared, including when returning from ${JSON.stringify(changed)}`, () => {
    const view = setup('lib/useConversationReferences.ts', 'useConversationReferences');
    const original = ['a', '甲'];
    view.render(original).addRef({ kind: 'asset', id: 'old.png', label: 'old.png' });
    assert.equal(view.render(original).refs.length, 1);
    const switched = view.render([changed.session, changed.persona]);
    assert.equal(switched.refs.length, 0);
    assert.equal(switched.refsFor(changed.session, changed.persona).length, 0);
    assert.equal(view.render(original).refs.length, 0);
    view.unmount();
  });
}

test('video cards use scoped Studio results and never link another project to that editor', async () => {
  const view = setup('components/VideoPage.tsx');
  const props = { session: 'session/with spaces', persona: '画像 & 甲', onMakeVideo() {} };
  view.render(props);
  const studios = view.pending.find(request => request.url.pathname === '/api/openfilm/studio');
  // The fixture resolves the project from the actual wire scope, with a deliberately different fallback.
  const matches = studios.url.searchParams.get('session') === props.session
    && studios.url.searchParams.get('persona') === props.persona;
  studios.reply(info(matches ? 'own' : 'wrong-scope'));
  view.pending.find(request => request.url.pathname === '/api/outputs').reply([
    { name: 'own', type: 'dir', openfilmProject: true, children: [{ name: 'own.mp4', type: 'file', path: 'own/own.mp4', kind: 'video' }] },
    { name: 'other', type: 'dir', openfilmProject: true, children: [{ name: 'other.mp4', type: 'file', path: 'other/other.mp4', kind: 'video' }] },
  ]);
  await settle();
  const cards = nodes(view.render(props), 'div').filter(node => node.props.className === 'gcard-meta');
  assert.equal(nodes(cards[0], 'a')[0]?.props.href, 'https://studio.test/own');
  assert.equal(nodes(cards[1], 'a').length, 0);
  view.unmount();
});

test('starting a new conversation before a render cannot send the previous conversations references', () => {
  const view = setup('lib/useConversationReferences.ts', 'useConversationReferences');
  view.render(['a', '甲']).addRef({ kind: 'asset', id: 'old.png', label: 'old.png' });
  const current = view.render(['a', '甲']);
  assert.equal(current.refsFor('a', '甲').length, 1);
  assert.equal(current.refsFor('b', '甲').length, 0);
  assert.equal(current.refsFor('a', '乙').length, 0);
  view.unmount();
});

test('general mode references are sent for both empty and absent persona values and cleared immediately', () => {
  const view = setup('lib/useConversationReferences.ts', 'useConversationReferences');
  view.render(['a', '']).addRef({ kind: 'asset', id: 'own.png', label: 'own.png' });
  const current = view.render(['a', '']);
  assert.equal(current.refsFor('a', undefined).length, 1);
  current.clearRefs();
  assert.equal(current.refsFor('a', '').length, 0);
  view.unmount();
});

test('selected layers retain distinct locations, boxes, times and complete project context', async () => {
  const view = setup('components/VideoStudioPanel.tsx');
  const refs = [];
  const props = { session: 'a', persona: '甲', onAddRef: ref => refs.push(ref) };
  view.render(props);
  view.pending[0].reply(info('project'));
  await settle();
  const source = {};
  nodes(view.render(props), 'iframe')[0].props.ref.current = { contentWindow: source };
  const layer = { kind: 'layer', clipId: 'shot-1', loc: 'film.html:12', time: 2.3,
    box: { x: 10, y: 20, width: 50, height: 30 }, text: '标题', source: '<h1>标题</h1>', path: 'film.html' };
  const selections = [layer, { ...layer, loc: 'film.html:13' },
    { ...layer, box: { ...layer.box, x: 80 } }, { ...layer, time: 3.5 },
    Object.fromEntries(Object.entries(layer).reverse())];
  for (const ref of selections) for (const listener of view.listeners) {
    listener({ data: { source: 'openfilm-studio', type: 'openfilm:reference', ref }, source, origin: 'https://studio.test' });
  }
  assert.equal(new Set(refs.slice(0, 4).map(ref => ref.id)).size, 4);
  assert.equal(refs[0].id, refs[4].id);
  assert.deepEqual(JSON.parse(refs[0].detail), {
    context: { session: 'a', persona: '甲', project: 'project' }, reference: layer,
  });
  view.unmount();
});

test('inline video preview reloads revisions and hides the previous persona before effects', async () => {
  const view = setup('components/ArtifactGallery.tsx');
  const props = { artifacts: [], isVideoSession: true, videoProduct: true, sessionKey: 'a', persona: '甲' };
  view.render(props);
  view.pending[0].reply({ ...info('project'), player: 'https://studio.test/player', rev: 'v1' });
  await settle();
  const original = nodes(view.render(props), 'iframe')[0];
  for (const callback of view.timers.values()) callback();
  view.pending.at(-1).reply({ ...info('project'), player: original.props.src, rev: 'v2' });
  await settle();
  const updated = nodes(view.render(props), 'iframe')[0];
  assert.equal(updated.props.src, original.props.src);
  assert.notEqual(updated.key, original.key);
  assert.equal(nodes(view.render({ ...props, persona: '乙' }, false), 'iframe').length, 0);
  view.unmount();
});
