import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import { nodes } from './helpers/openfilmHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/components/SkillDrawer.tsx', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
});

for (const scenario of [
  { name: 'image without API can try the agent', env: 'IMG_API_KEY', configured: false, input: '生成封面', blocked: false },
  { name: 'configured image API remains runnable', env: 'IMG_API_KEY', configured: true, input: '生成封面', blocked: false },
  { name: 'other media still require their API', env: 'VIDEO_API_KEY', configured: false, input: '生成视频', blocked: true },
  { name: 'image fallback still requires input', env: 'IMG_API_KEY', configured: false, input: '', blocked: true },
]) {
  test(scenario.name, () => {
    const detail = {
      needsApi: true, apiConfigured: scenario.configured, body: '',
      apiSpec: {
        label: '媒体 API', settings: [],
        providers: [{ id: 'provider', name: 'Provider', keys: [{
          env: scenario.env, label: 'Key', required: true, secret: true,
          configured: scenario.configured, masked: '', choices: [],
        }] }],
      },
    };
    let slot = 0;
    const exports = {};
    const jsx = (type, props) => ({ type, props });
    runInNewContext(outputText, {
      exports,
      require(name) {
        if (name === 'react') return {
          useState: initial => [slot++ === 0 ? detail : slot === 6 ? scenario.input : initial, () => {}],
          useEffect: () => {}, useMemo: callback => callback(), useRef: () => ({ current: 0 }),
        };
        if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
        if (name === '../lib/api') return {};
        if (name === '../lib/sanitize') return { renderMarkdown: () => '' };
        if (name === '../lib/skillDisplayNames') return { displayName: value => value };
        throw new Error(`Unexpected import: ${name}`);
      },
    });

    const tree = exports.default({ skillName: 'media', persona: '', onClose() {}, onConfigured() {} });

    const run = nodes(tree, 'button').find(node => node.props.children === '执行');
    assert.equal(run?.props.disabled, scenario.blocked);
  });
}
