import type { EnvTool } from './api';

export type LocalWhisperStatus =
  | { readonly state: 'detecting'; readonly label: '检测中' }
  | { readonly state: 'missing'; readonly label: '未装（去环境安装）' }
  | { readonly state: 'runtime-only'; readonly label: '运行库已装 · 缺 large-v3 模型' }
  | { readonly state: 'model-only'; readonly label: 'large-v3 模型已装 · 缺 faster-whisper' }
  | { readonly state: 'ready'; readonly label: '✓ 已就绪' };

export function localWhisperStatus(tools: readonly EnvTool[]): LocalWhisperStatus {
  if (tools.length === 0) return { state: 'detecting', label: '检测中' };
  const runtimeReady = tools.find((tool) => tool.id === 'fw')?.state === 'ok';
  const modelReady = tools.find((tool) => tool.id === 'model')?.state === 'ok';
  if (runtimeReady && modelReady) return { state: 'ready', label: '✓ 已就绪' };
  if (runtimeReady) return { state: 'runtime-only', label: '运行库已装 · 缺 large-v3 模型' };
  if (modelReady) return { state: 'model-only', label: 'large-v3 模型已装 · 缺 faster-whisper' };
  return { state: 'missing', label: '未装（去环境安装）' };
}
