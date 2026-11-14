import { expect, test } from 'bun:test';
import type { EnvTool } from '../src/lib/api';
import { localWhisperStatus } from '../src/lib/localWhisperStatus';

function tool(id: string, state: EnvTool['state']): EnvTool {
  return {
    id,
    state,
    version: null,
    detail: null,
    name: id,
    group: 'rm',
    desc: '',
  };
}

test.each([
  [[], 'detecting', '检测中'],
  [[tool('fw', 'ok'), tool('model', 'missing')], 'runtime-only', '运行库已装 · 缺 large-v3 模型'],
  [[tool('fw', 'missing'), tool('model', 'ok')], 'model-only', 'large-v3 模型已装 · 缺 faster-whisper'],
  [[tool('fw', 'ok'), tool('model', 'ok')], 'ready', '✓ 已就绪'],
] as const)('%# resolves local Whisper from runtime and model independently', (tools, state, label) => {
  expect(localWhisperStatus(tools)).toEqual({ state, label });
});
