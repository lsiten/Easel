import { text } from './aiWorkspaceApi';
import type { Document } from './aiWorkspaceApi';

const STATES: Readonly<Record<string, string>> = {
  submitted: '已提交', pending: '等待处理', cancelled: '已取消', canceled: '已取消', done: '已完成',
  queued: '排队中', running: '处理中', generating: '生成中', submitting: '提交中', waiting: '等待结果',
  processing: '处理中', in_progress: '处理中', completed: '已完成', succeeded: '已完成', success: '已完成',
  failed: '失败', error: '失败', aborted: '已停止', requires_action: '等待操作', unknown: '结果待确认',
};
export function jobState(value: unknown): string {
  const state = text(value);
  return STATES[state.toLowerCase()] || (state ? '状态待确认' : '');
}
export function modelChoices(model: Document | undefined, ...keys: readonly string[]): readonly string[] {
  for (const key of keys) {
    const value = model?.[key];
    if (Array.isArray(value) && value.length) return value.filter((item) => typeof item === 'string' || typeof item === 'number').map(text);
  }
  return [];
}

export const GENERATION_TYPES = [
  ['', '全部'], ['agent', '对话任务'], ['image', '图片'], ['video', '视频'], ['chat', '模型对话'],
  ['aideo', '智能视频'], ['draft-generation', '批量草稿'], ['character', '角色'],
] as const;

export function contentGroupName(identifier: unknown, groups: readonly Document[]): string {
  const id = text(identifier);
  if (!id) return '默认分组';
  const group = groups.find((entry) => entry.id === id);
  return group ? text(group.name) || '默认分组' : '原分组不可用';
}

export function publicShareError(error: string): string {
  if (!error) return '';
  return error === 'agent_task_not_found'
    ? '分享无法访问，链接可能已过期或被删除。'
    : '暂时无法加载分享，请检查网络后重试。';
}
