import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/components/workspace/PlatformPublishingPanel.tsx', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  },
});
const settle = () => new Promise(resolve => setImmediate(resolve));
const automaticPolicy = { mode: 'automated', automaticSubmission: true };
const manualPolicy = { mode: 'manual', automaticSubmission: false };

function setup(responses = [automaticPolicy]) {
  const state = [];
  const effects = [];
  let cursor = 0;
  let pending = [];
  let requests = 0;
  const createElement = (type, props) => ({ type, props });
  const modules = {
    react: {
      useState(initial) {
        const index = cursor++;
        if (!(index in state)) state[index] = initial;
        return [state[index], value => {
          state[index] = typeof value === 'function' ? value(state[index]) : value;
        }];
      },
      useEffect(callback, dependencies) {
        const index = cursor++;
        const previous = effects[index];
        if (!previous || dependencies.some((value, position) => value !== previous.dependencies[position])) {
          pending.push(() => {
            previous?.cleanup?.();
            effects[index] = { dependencies, cleanup: callback() };
          });
        }
      },
    },
    'react/jsx-runtime': { jsx: createElement, jsxs: createElement },
    '../../lib/api': {
      fetchPublishingPolicy() {
        const response = responses[requests++];
        return response instanceof Error ? Promise.reject(response) : Promise.resolve(response);
      },
    },
    '../../lib/publishingPolicy': {
      automaticPublishingAllowed: policy => policy.mode === 'automated' && policy.automaticSubmission,
      resolvePublishingMode: (policy, requested) =>
        policy.mode === 'automated' && policy.automaticSubmission && requested === 'automated' ? 'automated' : 'manual',
    },
    '../ManualPublishPage': { __esModule: true, default: 'manual-page' },
  };
  const exports = {};
  runInNewContext(outputText, { exports, require: name => modules[name] });
  return {
    render() {
      cursor = 0;
      pending = [];
      const tree = exports.PlatformPublishingPanel({
        persona: 'travel-brand',
        platform: 'xiaohongshu',
        automaticLabel: '浏览器自动发布',
        automatic: { type: 'automatic-page', props: { persona: 'travel-brand' } },
      });
      pending.forEach(callback => callback());
      return tree;
    },
    unmount() {
      effects.forEach(effect => effect?.cleanup?.());
    },
    requests: () => requests,
  };
}

function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...nodes(tree.props?.children)];
}

function option(tree, value) {
  const result = nodes(tree).find(node => node.type === 'option' && node.props.value === value);
  assert.ok(result, `Missing option: ${value}`);
  return result;
}

test('automatic policy keeps the platform automatic workflow and exposes manual review', async () => {
  const app = setup();
  assert.equal(app.render().props.children, '正在读取发布方式…');
  await settle();
  const tree = app.render();
  assert.equal(option(tree, 'automated').props.disabled, false);
  assert.equal(nodes(tree).some(node => node.type === 'automatic-page'), true);
  assert.equal(nodes(tree).some(node => node.type === 'manual-page'), false);
});

test('switching publish mode changes only the visible workflow', async () => {
  const app = setup();
  app.render();
  await settle();
  let tree = app.render();
  const select = nodes(tree).find(node => node.type === 'select');
  select.props.onChange({ target: { value: 'manual' } });
  tree = app.render();
  const manual = nodes(tree).find(node => node.type === 'manual-page');
  assert.equal(manual.props.persona, 'travel-brand');
  assert.equal(manual.props.platform, 'xiaohongshu');
  assert.equal(nodes(tree).some(node => node.type === 'automatic-page'), false);
  select.props.onChange({ target: { value: 'automated' } });
  tree = app.render();
  assert.equal(nodes(tree).some(node => node.type === 'automatic-page'), true);
  assert.equal(app.requests(), 1);
});

test('manual-only policy disables automatic publishing', async () => {
  const app = setup([manualPolicy]);
  app.render();
  await settle();
  const tree = app.render();
  assert.equal(option(tree, 'automated').props.disabled, true);
  assert.equal(nodes(tree).some(node => node.type === 'manual-page'), true);
});

test('inconsistent policy never exposes automatic publishing', async () => {
  for (const policy of [
    { mode: 'automated', automaticSubmission: false },
    { mode: 'manual', automaticSubmission: true },
  ]) {
    const app = setup([policy]);
    app.render();
    await settle();
    const tree = app.render();
    assert.equal(option(tree, 'automated').props.disabled, true);
    assert.equal(nodes(tree).some(node => node.type === 'manual-page'), true);
  }
});

test('policy lookup failure blocks both workflows until a successful retry', async () => {
  const app = setup([new Error('network unavailable'), automaticPolicy]);
  app.render();
  await settle();
  let tree = app.render();
  assert.equal(tree.props.role, 'alert');
  assert.equal(nodes(tree).some(node => node.type === 'automatic-page'), false);
  nodes(tree).find(node => node.type === 'button').props.onClick();
  app.render();
  await settle();
  tree = app.render();
  assert.equal(nodes(tree).some(node => node.type === 'automatic-page'), true);
  assert.equal(app.requests(), 2);
});

test('policy lookup completing after unmount does not update the panel', async () => {
  let resolve;
  const response = new Promise(callback => { resolve = callback; });
  const app = setup([response]);
  app.render();
  app.unmount();
  resolve(automaticPolicy);
  await settle();
  assert.equal(app.render().props.children, '正在读取发布方式…');
});
