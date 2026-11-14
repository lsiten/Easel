import assert from 'node:assert/strict';
import { test, afterEach } from 'node:test';
import { browserPlatformFor, platformConnections, preferredConnection } from '../../web/frontend/src/lib/platformConnections.ts';
import { loadPublishDraft, savePublishDraft } from '../../web/frontend/src/lib/store.ts';

const browser = { platform: 'xiaohongshu', name: '小红书', backend: 'xhs', supported: true, loggedIn: true, note: '' };
const application = { platform: 'rednote-link', display_name: '小红书应用分享', configured: false };

test('an existing browser login is usable when the application has no configuration', () => {
  const rows = platformConnections([browser], [application]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, 'xiaohongshu');
  assert.equal(rows[0].browser.loggedIn, true);
  assert.equal(rows[0].application.platform, 'rednote-link');
  assert.equal(preferredConnection(rows[0]), 'browser');
});

test('API-only and browser-only platforms retain their usable connection choice', () => {
  assert.equal(browserPlatformFor('rednote-link'), 'xiaohongshu');
  assert.equal(browserPlatformFor('zhihu'), 'zhihu');
  const rows = platformConnections([{ ...browser, platform: 'zhihu', name: '知乎' }], [{ ...application, platform: 'youtube', configured: true }]);
  assert.equal(preferredConnection(rows.find(row => row.id === 'zhihu')), 'browser');
  assert.equal(preferredConnection(rows.find(row => row.id === 'youtube')), 'application');
  const signedOut = platformConnections([{ ...browser, loggedIn: false }], [application])[0];
  assert.equal(preferredConnection(signedOut), 'application');
});

test('workbench browser drafts remain isolated by persona and platform', () => {
  const store = new Map();
  globalThis.localStorage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) };
  const draft = { title: 'Alice draft', body: 'Body', platforms: ['xiaohongshu'], overrides: {}, tags: '' };
  const scope = JSON.stringify(['alice', 'xiaohongshu']);
  savePublishDraft(draft, scope);
  assert.equal(loadPublishDraft(scope).title, 'Alice draft');
  assert.equal(loadPublishDraft(JSON.stringify(['bob', 'xiaohongshu'])).title, '');
  assert.equal(loadPublishDraft(JSON.stringify(['alice', 'douyin'])).title, '');
  assert.equal(loadPublishDraft().title, '');
});
afterEach(() => { delete globalThis.localStorage; });
