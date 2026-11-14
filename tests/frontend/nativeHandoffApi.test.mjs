import assert from 'node:assert/strict';
import { test, afterEach } from 'node:test';
import { handoffActionUrl, handoffMediaUrl, ownerHandoffUrl, rednoteShareRequest, loadRednoteSdk, REDNOTE_SDK_SOURCE }
  from '../../web/frontend/src/lib/nativeHandoffApi.ts';

const id = 'a'.repeat(32), token = 'b'.repeat(64);
const mediaPath = `/api/native-handoff/templates/${id}/media/0?token=${token}`;
function browser() { globalThis.window = { location: { origin: 'https://easel.example', pathname: '/' } }; }
afterEach(() => { delete globalThis.window; delete globalThis.document; });

test('owner share links bind both template and token before navigation', () => {
  browser();
  const row = { template_id: id, token, path: `/?handoff=${id}&token=${token}` };
  assert.equal(ownerHandoffUrl(row), row.path);
  assert.equal(ownerHandoffUrl({ ...row, path: `/?handoff=${'c'.repeat(32)}&token=${token}` }), '');
  assert.equal(ownerHandoffUrl({ ...row, path: `${row.path}&redirect=https://foreign.example` }), '');
  assert.equal(ownerHandoffUrl({ ...row, public_url: `https://public.example${row.path}` }), `https://public.example${row.path}`);
  assert.equal(ownerHandoffUrl({ ...row, public_url: 'https://foreign.example/?handoff=other&token=other' }), row.path);
});

test('native app actions permit the source Douyin entry and reject other schemes or credentials', () => {
  assert.equal(handoffActionUrl('snssdk1128://openplatform/share?signature=approved'), 'snssdk1128://openplatform/share?signature=approved');
  for (const value of ['javascript:alert(1)', 'snssdk1128://other/share', 'snssdk1128://openplatform/other', 'https://user:pass@open.douyin.com/path', 'https://foreign.example']) assert.equal(handoffActionUrl(value), '');
});

test('Rednote SDK payload uses the exact signed media scope and declared native format', () => {
  browser();
  const verifyConfig = { appKey: 'app', nonce: 'nonce', timestamp: '123', signature: 'signed' };
  const image = rednoteShareRequest(verifyConfig, [{ kind: 'image', path: mediaPath }], id, token);
  assert.deepEqual(image, { verifyConfig, shareInfo: { type: 'normal', images: [`https://easel.example${mediaPath}`] } });
  const video = rednoteShareRequest(verifyConfig, [{ kind: 'video', path: mediaPath }], id, token);
  assert.deepEqual(video.shareInfo, { type: 'video', video: `https://easel.example${mediaPath}` });
  assert.equal(handoffMediaUrl(mediaPath, id, 'wrong-token'), '');
  assert.throws(() => rednoteShareRequest(verifyConfig, [{ kind: 'image', path: mediaPath }], 'wrong-template', token), /媒体地址/);
});

test('SDK loading is explicit, uses the official HTTPS source, and deduplicates concurrent requests', async () => {
  browser();
  const scripts = [];
  const sdk = { share() {} };
  globalThis.document = {
    createElement(tag) { assert.equal(tag, 'script'); return { remove() {} }; },
    head: { append(script) { scripts.push(script); queueMicrotask(() => { window.xhs = sdk; script.onload(); }); } },
  };
  const first = loadRednoteSdk(), second = loadRednoteSdk();
  assert.equal(first, second);
  assert.equal(await first, sdk);
  assert.equal(scripts.length, 1);
  assert.equal(scripts[0].src, REDNOTE_SDK_SOURCE);
  assert.equal(await loadRednoteSdk(), sdk);
  assert.equal(scripts.length, 1);
});

test('a failed SDK download rejects and leaves media-copy fallback available', async () => {
  browser();
  globalThis.document = { createElement() { return { remove() {} }; }, head: { append(script) { queueMicrotask(() => script.onerror()); } } };
  await assert.rejects(loadRednoteSdk(), /加载失败/);
  assert.equal(handoffMediaUrl(mediaPath, id, token), `https://easel.example${mediaPath}`);
});
