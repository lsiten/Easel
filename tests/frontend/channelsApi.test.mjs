import assert from 'node:assert/strict';
import { test } from 'node:test';
import { channelRows, channelWorkId, channelWorkDetails, channelNextCursor, scopedChannelPath, readChannelPlatforms }
  from '../../web/frontend/src/lib/channelsApi.ts';

test('YouTube search results retain their nested stable video identity', () => {
  const payload = { items: [{ id: { kind: 'youtube#video', videoId: 'video-1' }, snippet: { title: 'Current work' } }], nextPageToken: 'next-page' };
  assert.equal(channelWorkId(channelRows(payload)[0]), 'video-1');
  assert.equal(channelNextCursor(payload), 'next-page');
});

test('TikTok and Twitter work-detail envelopes expose the actual selected work', () => {
  const video = { id: 'video-2', title: 'Video title', share_url: 'https://www.tiktok.com/@creator/video/2' };
  assert.equal(channelWorkDetails({ data: { videos: [video] } }), video);
  assert.deepEqual(channelWorkDetails({ data: { id: 'tweet-1', text: 'Post body' } }), { id: 'tweet-1', text: 'Post body' });
  assert.equal(channelWorkId({ id: 'pin-1' }), 'pin-1');
});

test('pagination reads opaque provider cursors and stops at terminal pages', () => {
  assert.equal(channelNextCursor({ data: { has_more: true, cursor: 123 } }), '123');
  assert.equal(channelNextCursor({ data: { has_more: false, cursor: 123 } }), '');
  assert.equal(channelNextCursor({ meta: { next_token: 'tweet-page' } }), 'tweet-page');
  assert.equal(channelNextCursor({ bookmark: 'board-page' }), 'board-page');
  assert.equal(channelNextCursor({ paging: { next: 'https://provider.invalid/path?after=opaque', cursors: { after: 'opaque' } } }), 'opaque');
  assert.equal(channelNextCursor({ paging: { cursors: { after: 'terminal' } } }), '');
});

test('scope query retains Unicode persona and exact selected actor', () => {
  const path = scopedChannelPath('youtube', 'works', { persona: '工作画像', account_uid: 'actor+one' }, { cursor: 'a/b?token=x' });
  const url = new URL(path, 'http://localhost');
  assert.equal(url.searchParams.get('persona'), '工作画像');
  assert.equal(url.searchParams.get('account_uid'), 'actor+one');
  assert.equal(url.searchParams.get('cursor'), 'a/b?token=x');
});

test('native platform aliases retain their declared authorization and handoff modes', () => {
  const [douyin, rednote] = readChannelPlatforms([
    { platform: 'douyin-open', display_name: '抖音开放平台', auth_mode: 'miniapp_qr', app_handoff_supported: true, configured: false, publish_modes: ['image', 'video'] },
    { platform: 'rednote-link', auth_mode: 'app_handoff', app_handoff_supported: true, publish_modes: ['image', 'video'] },
  ]);
  assert.equal(douyin.auth_mode, 'miniapp_qr');
  assert.equal(douyin.configured, false);
  assert.equal(rednote.auth_mode, 'app_handoff');
  assert.equal(rednote.app_handoff_supported, true);
  assert.equal(rednote.display_name, '小红书应用分享');
});
