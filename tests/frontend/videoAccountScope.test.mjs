import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setup, nodes, settle } from './helpers/openfilmHarness.mjs';

const project = name => [{ name, path: name, type: 'dir', children: [
  { name: `${name}.mp4`, path: `${name}/${name}.mp4`, type: 'file', kind: 'video' },
] }];
const videoSources = tree => nodes(tree, 'video').map(video => video.props.src);
const outputs = view => view.pending.filter(request => request.url.pathname === '/api/outputs');

test('video account switches hide old cards and ignore responses from the previous account', async () => {
  const view = setup('components/VideoPage.tsx');
  const first = { session: 'chat', persona: '账号 & 甲', onMakeVideo() {} };
  const second = { ...first, persona: '账号乙' };
  const responseFor = request => project(request.url.searchParams.get('persona') === first.persona ? '甲的视频' : '乙的视频');
  view.render(first);
  outputs(view)[0].reply(responseFor(outputs(view)[0]));
  await settle();
  assert.deepEqual(videoSources(view.render(first)).map(decodeURIComponent), ['/api/media/甲的视频/甲的视频.mp4']);

  assert.equal(videoSources(view.render(second)).length, 0);
  const delayed = outputs(view).at(-1);
  assert.equal(videoSources(view.render(first)).length, 0);
  outputs(view).at(-1).reply(responseFor(outputs(view).at(-1)));
  await settle();
  delayed.reply(responseFor(delayed));
  await settle();
  assert.deepEqual(videoSources(view.render(first)).map(decodeURIComponent), ['/api/media/甲的视频/甲的视频.mp4']);
  view.unmount();
});
