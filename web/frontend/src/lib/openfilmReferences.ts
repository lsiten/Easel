import type { Reference } from './api';

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

function canonicalValue(value: unknown): JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, entry]) => [key, canonicalValue(entry)]));
  }
  return null;
}

/** Preserve the complete selection so elements in one clip remain independently addressable. */
export function normalizeOpenfilmRef(value: unknown, context: {
  readonly session?: string;
  readonly persona?: string;
  readonly project: string;
}): Reference | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const fields = Object.fromEntries(Object.entries(value));
  if (typeof fields.kind !== 'string' || !fields.kind) return null;
  const text = (entry: unknown) => typeof entry === 'string' ? entry : '';
  const seconds = (entry: unknown) => typeof entry === 'number' ? `${entry}s` : '';
  let kind = 'openfilm-scene';
  let label = text(fields.label);
  switch (fields.kind) {
    case 'clip': label ||= text(fields.src).split('/').pop() || '镜头'; break;
    case 'layer': label ||= text(fields.text) || text(fields.tag) || '元素'; break;
    case 'track': kind = 'openfilm-track'; label ||= `轨道 ${fields.track ?? ''}`; break;
    case 'file': kind = 'asset'; label ||= text(fields.path).split('/').pop() || '素材'; break;
    case 'region': label ||= `${seconds(fields.time)} 画面`; break;
    case 'range': label ||= `${seconds(fields.start)}-${seconds(fields.end)} 片段`; break;
    case 'time': label ||= `${seconds(fields.time)} 时刻`; break;
    case 'subtitle': label ||= text(fields.text) || '字幕'; break;
    case 'node': kind = 'asset'; label ||= '节点'; break;
    case 'example': kind = 'asset'; label ||= text(fields.title) || '示例'; break;
    case 'voice': kind = 'asset'; label ||= text(fields.name) || '声音'; break;
    default: kind = fields.kind; label ||= '引用';
  }
  const detail = JSON.stringify(canonicalValue({ context, reference: value }));
  return { kind, id: `openfilm:${detail}`, label, detail };
}
