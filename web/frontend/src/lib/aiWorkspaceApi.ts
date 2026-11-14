import { channelDocument, channelRows, channelText, channelRequest } from './channelsApi';
export { channelDocument as document, channelRows as rows, channelText as text } from './channelsApi';
export type { ChannelDocument as Document } from './channelsApi';

export function aiPath(path: string, persona: string, query: Readonly<Record<string, string>> = {}): string {
  const optional = Object.fromEntries(Object.entries(query).filter(([, value]) => value !== ''));
  return `${path}?${new URLSearchParams({ persona, ...optional })}`;
}
export async function aiRequest(path: string, signal: AbortSignal, payload?: unknown, method = 'POST'): Promise<unknown> {
  return channelRequest(path, payload === undefined ? { signal } : {
    signal, method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
}
export async function agentSubmit(path: string, signal: AbortSignal, payload: unknown): Promise<string> {
  const base = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '');
  const response = await fetch(base + path, { method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (!response.ok) throw new Error(`任务提交失败（${response.status}）`);
  const reader = response.body?.getReader();
  if (!reader) throw new Error('任务流不可用');
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) throw new Error('任务流缺少任务编号');
      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split('\n'); buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = channelDocument(JSON.parse(line.slice(6)));
        if (data.type === 'init' && channelText(data.taskId)) return channelText(data.taskId);
      }
    }
  } finally { await reader.cancel(); }
}
export type PageData = { readonly items: ReturnType<typeof channelRows>; readonly total: number };
export function readPage(value: unknown): PageData {
  const data = channelDocument(value);
  if (typeof data.total !== 'number') throw new Error('列表返回格式无效');
  return { items: channelRows(data.items), total: data.total };
}
export function mediaUrl(value: unknown): string {
  const url = channelText(value);
  if (/^\/outputs\//.test(url) || /^\/api\/media\//.test(url)) return url;
  if (url && !url.startsWith('/') && !url.includes(':') && !url.split('/').includes('..')) return '/api/media/' + url.split('/').map(encodeURIComponent).join('/');
  try { const parsed = new URL(url); return ['https:', 'http:'].includes(parsed.protocol) ? parsed.href : ''; }
  catch { return ''; }
}
