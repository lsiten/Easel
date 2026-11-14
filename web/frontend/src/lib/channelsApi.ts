export type ChannelDocument = Readonly<Record<string, unknown>>;
export type ChannelPlatform = {
  readonly platform: string;
  readonly display_name: string;
  readonly auth_mode: 'oauth2' | 'miniapp_qr' | 'app_handoff' | 'unsupported';
  readonly app_handoff_supported: boolean;
  readonly update_supported: boolean;
  readonly account_binding: string;
  readonly work_list_supported: boolean;
  readonly work_detail_supported: boolean;
  readonly cover_supported: boolean;
  readonly cover_required: boolean;
  readonly configured: boolean;
  readonly publish_modes: readonly string[];
  readonly works: boolean;
  readonly analytics: boolean;
  readonly engagement: boolean;
  readonly limits: ChannelDocument;
  readonly media_rules: ChannelDocument;
  readonly option_fields: readonly string[];
};
export type ChannelAccount = {
  readonly account_uid: string;
  readonly display_name: string;
  readonly expires_at: number | null;
};
export type ChannelScope = { readonly persona: string; readonly account_uid: string };
export type ChannelRun = {
  readonly run_id: string;
  readonly state: string;
  readonly platform_work_id: string;
  readonly permalink: string;
  readonly error: string;
  readonly visibility: string;
  readonly work_link_status: string;
  readonly cover_status: string;
  readonly cover_error: string;
  readonly requires_user_action: boolean;
  readonly action_url: string;
  readonly action_expires_at: number;
};
export type ChannelApproval = { readonly approval_digest: string; readonly approval_expires_at: number };

export const CHANNEL_NAMES: Readonly<Record<string, string>> = {
  youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram', facebook: 'Facebook',
  threads: 'Threads', twitter: 'X / Twitter', linkedin: 'LinkedIn', pinterest: 'Pinterest',
  'google-business': 'Google Business',
  'bilibili-open': '哔哩哔哩开放平台', 'kwai-open': '快手开放平台', 'douyin-open': '抖音开放平台',
  'wechat-official-open': '微信公众号开放协议', 'wechat-channels-open': '微信视频号作品接入', 'rednote-link': '小红书应用分享',
};

export function channelDocument(value: unknown): ChannelDocument {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) return Object.fromEntries(Object.entries(value));
  throw new Error('平台返回的数据格式无效');
}
export function channelText(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}
export function channelRows(value: unknown): readonly ChannelDocument[] {
  if (Array.isArray(value)) return value.filter((item): item is ChannelDocument => item !== null && typeof item === 'object' && !Array.isArray(item));
  if (value !== null && typeof value === 'object') {
    const document = channelDocument(value);
    for (const key of ['items', 'data', 'videos', 'comments', 'elements', 'records', 'rows', 'tasks']) {
      if (Array.isArray(document[key])) return channelRows(document[key]);
      if (document[key] !== null && typeof document[key] === 'object') {
        const nested = channelRows(document[key]);
        if (nested.length) return nested;
      }
    }
  }
  return [];
}
export function channelWorkId(row: ChannelDocument): string {
  const identity = row.id;
  const video = identity !== null && typeof identity === 'object' && !Array.isArray(identity) ? channelDocument(identity).videoId : '';
  return channelText(row.platform_work_id || video || identity || row.video_id || row.name);
}
export function channelWorkDetails(value: unknown): ChannelDocument {
  const root = channelDocument(value);
  const data = root.data;
  if (data !== null && typeof data === 'object' && !Array.isArray(data)) {
    const document = channelDocument(data);
    return channelRows(document.videos)[0] || document;
  }
  return root;
}
export function channelNextCursor(value: unknown): string {
  const root = channelDocument(value);
  const paging = root.paging ? channelDocument(root.paging) : {};
  const meta = root.meta ? channelDocument(root.meta) : {};
  const data = root.data && !Array.isArray(root.data) ? channelDocument(root.data) : {};
  const cursors = paging.cursors ? channelDocument(paging.cursors) : {};
  return channelText(root.nextPageToken || root.bookmark || meta.next_token ||
    (paging.next ? cursors.after : '') || (data.has_more === true ? data.cursor : ''));
}
export function channelError(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请重试';
}
export function channelJson(text: string): ChannelDocument {
  const value: unknown = JSON.parse(text || '{}');
  return channelDocument(value);
}
export function channelUrl(value: unknown): string {
  if (typeof value !== 'string') return '';
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch (error) {
    if (error instanceof TypeError) return '';
    throw error;
  }
}

export async function channelRequest(path: string, init: RequestInit = {}): Promise<unknown> {
  const base = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '');
  const response = await fetch(`${base}${path}`, { cache: 'no-store', signal: AbortSignal.timeout(900_000), ...init });
  const payload: unknown = response.status === 204 ? {} : await response.json();
  if (!response.ok) {
    const body = channelDocument(payload);
    const detail = body.detail;
    const message = typeof detail === 'string' ? detail
      : detail !== null && typeof detail === 'object' && !Array.isArray(detail)
        ? channelText(channelDocument(detail).code) : channelText(body.message);
    throw new Error(message || `请求失败（${response.status}）`);
  }
  return payload;
}
export function channelWrite(path: string, payload: ChannelDocument, method = 'POST', signal?: AbortSignal): Promise<unknown> {
  return channelRequest(path, { method, signal: signal ?? AbortSignal.timeout(900_000),
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
}
export function scopedChannelPath(platform: string, suffix: string, scope: ChannelScope, extra: Readonly<Record<string, string>> = {}): string {
  return `/api/channels/${encodeURIComponent(platform)}/${suffix}?${new URLSearchParams({ ...scope, ...extra })}`;
}
export function readChannelRun(value: unknown): ChannelRun {
  const row = channelDocument(value);
  if (typeof row.run_id !== 'string' || typeof row.state !== 'string') throw new Error('发布记录格式无效');
  return { run_id: row.run_id, state: row.state, platform_work_id: channelText(row.platform_work_id),
    permalink: channelUrl(row.permalink), error: channelText(row.error), visibility: channelText(row.visibility),
    work_link_status: channelText(row.work_link_status), cover_status: channelText(row.cover_status), cover_error: channelText(row.cover_error),
    requires_user_action: row.requires_user_action === true, action_url: channelText(row.action_url),
    action_expires_at: typeof row.action_expires_at === 'number' ? row.action_expires_at : 0 };
}
export function readChannelApproval(value: unknown): ChannelApproval {
  const row = channelDocument(value);
  if (typeof row.approval_digest !== 'string' || typeof row.approval_expires_at !== 'number') throw new Error('审核凭据格式无效');
  return { approval_digest: row.approval_digest, approval_expires_at: row.approval_expires_at };
}
export function readChannelPlatforms(value: unknown): readonly ChannelPlatform[] {
  return channelRows(value).map((row) => ({ platform: channelText(row.platform), configured: row.configured === true,
    display_name: channelText(row.display_name) || CHANNEL_NAMES[channelText(row.platform)] || channelText(row.platform),
    auth_mode: row.auth_mode === 'miniapp_qr' || row.auth_mode === 'app_handoff' || row.auth_mode === 'oauth2' ? row.auth_mode : row.auth_mode ? 'unsupported' : 'oauth2',
    app_handoff_supported: row.app_handoff_supported === true, update_supported: row.update_supported === true,
    account_binding: channelText(row.account_binding),
    work_list_supported: typeof row.work_list_supported === 'boolean' ? row.work_list_supported : row.works === true,
    work_detail_supported: typeof row.work_detail_supported === 'boolean' ? row.work_detail_supported : row.works === true,
    cover_supported: row.cover_supported === true, cover_required: row.cover_required === true,
    publish_modes: Array.isArray(row.publish_modes) ? row.publish_modes.filter((mode): mode is string => typeof mode === 'string') : [],
    works: row.works === true, analytics: row.analytics === true, engagement: row.engagement === true,
    limits: row.limits ? channelDocument(row.limits) : {}, media_rules: row.media_rules ? channelDocument(row.media_rules) : {},
    option_fields: Array.isArray(row.option_fields) ? row.option_fields.filter((field): field is string => typeof field === 'string') : [] }));
}
export function readChannelAccounts(value: unknown): readonly ChannelAccount[] {
  return channelRows(value).map((row) => ({ account_uid: channelText(row.account_uid), display_name: channelText(row.display_name),
    expires_at: typeof row.expires_at === 'number' ? row.expires_at : null }));
}
