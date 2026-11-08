import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { runInNewContext } from 'node:vm';

const require = createRequire(new URL('../../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const root = new URL('../../../web/frontend/src/', import.meta.url).pathname;
export const settle = () => new Promise(resolve => setImmediate(resolve));

// Execute the shipped components with deterministic hook scheduling and a wire-level fixture.
export function setup(file, exportName = 'default') {
  let slots = [];
  const effects = [];
  const pending = [];
  const listeners = new Set();
  const timers = new Map();
  const modules = new Map();
  let cursor = 0;
  const react = {
    createContext: value => ({ value }),
    useContext: context => context.value,
    useState(initial) {
      const storage = slots;
      const index = cursor++;
      if (!(index in storage)) storage[index] = typeof initial === 'function' ? initial() : initial;
      return [storage[index], update => { storage[index] = typeof update === 'function' ? update(storage[index]) : update; }];
    },
    useRef(initial) {
      const index = cursor++;
      return slots[index] ??= { current: initial };
    },
    useMemo(callback, deps) {
      const index = cursor++;
      if (!slots[index] || deps.some((dep, i) => !Object.is(dep, slots[index].deps[i]))) {
        slots[index] = { deps, value: callback() };
      }
      return slots[index].value;
    },
    useCallback(callback, deps) { return react.useMemo(() => callback, deps); },
    useEffect(callback, deps) {
      const storage = slots;
      const index = cursor++;
      if (!slots[index] || deps.some((dep, i) => !Object.is(dep, slots[index].deps[i]))) {
        effects.push(() => {
          storage[index]?.cleanup?.();
          storage[index] = { deps, cleanup: callback() };
        });
      }
    },
  };
  const jsx = (type, props, key) => ({ type, props, key });
  const window = {
    location: { pathname: '/', origin: 'http://easel.test' },
    addEventListener: (type, callback) => { if (type === 'message') listeners.add(callback); },
    removeEventListener: (type, callback) => listeners.delete(callback),
    setInterval: callback => { const id = Symbol(); timers.set(id, callback); return id; },
    clearInterval: id => timers.delete(id),
    setTimeout(callback) {
      const id = Symbol();
      timers.set(id, () => { timers.delete(id); callback(); });
      return id;
    },
    clearTimeout: id => timers.delete(id),
  };
  function load(path) {
    if (modules.has(path)) return modules.get(path);
    const exports = {};
    modules.set(path, exports);
    const source = readFileSync(path, 'utf8');
    const { outputText } = ts.transpileModule(source, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    });
    runInNewContext(outputText, {
      exports, window, URL, AbortController, console,
      setInterval: window.setInterval, clearInterval: window.clearInterval,
      setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
      fetch: (url, options) => new Promise(resolve => pending.push({
        url: new URL(url, window.location.origin), options,
        reply: data => resolve({ ok: true, json: async () => data }),
      })),
      require(name) {
        if (name === 'react') return react;
        if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
        if (name.endsWith('/icons')) return {};
        const base = resolve(dirname(path), name);
        return load(base + (existsSync(base + '.ts') ? '.ts' : '.tsx'));
      },
    });
    return exports;
  }
  function mount(file, exportName = 'default') {
    const ownSlots = [];
    const component = load(resolve(root, file))[exportName];
    return {
      pending, listeners, timers, mount,
      provideStudio(value) {
        load(resolve(root, 'lib/useOpenfilmStudio.ts')).OpenfilmStudioContext.value = value;
      },
      render(props, commit = true) {
        slots = ownSlots;
        cursor = 0;
        const parent = Array.isArray(props) ? component(...props) : component(props);
        const tree = typeof parent?.type === 'function' ? parent.type(parent.props) : parent;
        if (commit) while (effects.length) effects.shift()();
        return tree;
      },
      unmount() { ownSlots.forEach(slot => slot?.cleanup?.()); },
    };
  }
  return mount(file, exportName);
}

export function nodes(tree, type) {
  if (!tree || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap(child => nodes(child, type));
  return [...(tree.type === type ? [tree] : []), ...nodes(tree.props?.children, type)];
}
export const info = topic => ({ available: false, has_video: true, topic, studio: `https://studio.test/${topic}` });

