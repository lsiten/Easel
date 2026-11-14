import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const jsxRuntime = require('react/jsx-runtime');
const source = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
});

function mountApp() {
  const slots = [];
  let cursor = 0;
  const sessions = ['first', 'second'].map(id => ({ id, title: id, video: true, messages: [] }));
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], update => { slots[index] = typeof update === 'function' ? update(slots[index]) : update; }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useEffect() {},
    useCallback(callback) { return callback; },
  };
  const exports = {};
  runInNewContext(outputText, {
    exports, console, localStorage: { getItem() { return null; } },
    require(name) {
      if (name === 'react') return react;
      if (name === 'react/jsx-runtime') return jsxRuntime;
      if (name === './lib/store') return { loadSessions: () => sessions, saveSessions() {} };
      if (name === './lib/api') return {};
      if (name === './lib/navigation') return {
        normalizePage: page => page,
        pageFromHash: () => null,
        routeForPage: () => '#/workspace/overview',
        moduleForPage: () => 'workspace',
      };
      if (name === './components/AppPageContent') return { AppPageContent: './components/AppPageContent' };
      if (name === './lib/useConversationReferences') return {
        useConversationReferences: () => ({ refs: [], clearRefs() {}, refsFor: () => [] }),
      };
      if (name === './lib/useOpenfilmStudio') return {
        OpenfilmStudioContext: { Provider: 'StudioProvider' },
        useOpenfilmStudio: () => ({ studio: { has_video: true } }),
      };
      return { __esModule: true, default: name };
    },
  });
  return () => { cursor = 0; return exports.default(); };
}

function find(tree, predicate) {
  if (!tree || typeof tree !== 'object') return undefined;
  if (Array.isArray(tree)) return tree.map(child => find(child, predicate)).find(Boolean);
  return predicate(tree) ? tree : find(tree.props?.children, predicate);
}

test('conversation and Studio siblings have independent keys across session and persona switches', () => {
  const render = mountApp();
  const select = id => {
    const sidebar = find(render(), node => node.type === './components/Sidebar');
    sidebar.props.onSessionSelect(id);
    return find(render(), node => node.props?.className?.startsWith('page-host'));
  };
  const original = select('first');
  const changed = select('second');
  const sidebar = find(render(), node => node.type === './components/Sidebar');
  sidebar.props.onPersonaChange('新画像');
  const personaChanged = find(render(), node => node.props?.className?.startsWith('page-host'));

  for (const host of [original, changed, personaChanged]) {
    const keys = host.props.children.filter(child => child && child.key !== null).map(child => child.key);
    assert.equal(new Set(keys).size, keys.length, 'siblings must never share a reconciliation key');
  }
  const chat = host => {
    const page = find(host, node => node.type === './components/AppPageContent');
    return find(page.props.chat, node => node.type === './components/ChatPage');
  };
  assert.notEqual(chat(original).key, chat(changed).key);
  assert.notEqual(chat(changed).key, chat(personaChanged).key);
});
