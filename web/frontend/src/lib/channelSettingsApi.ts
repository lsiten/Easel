import { channelDocument, channelRequest, channelRows, channelText, channelUrl, channelWrite } from './channelsApi';
import type { ChannelDocument } from './channelsApi';

export const AI_KINDS = ['image', 'edit', 'video', 'agent'] as const;
export type AiKind = typeof AI_KINDS[number];
export const AI_LABELS: Readonly<Record<AiKind, string>> = { image: '图片生成', edit: '图片编辑', video: '视频生成', agent: '创作助手' };
export type AiPreference = { readonly default_model: string; readonly options: ChannelDocument };
export type UserLocation = {
  readonly lat: number; readonly lng: number;
  readonly country: string; readonly country_code: string; readonly city: string;
  readonly city_code: string; readonly district: string; readonly district_code: string;
};
export type LocalProfile = {
  readonly persona: string; readonly name: string; readonly avatar: string;
  readonly locale: 'zh-CN' | 'en-US'; readonly user_type: 'creator' | 'business_owner';
  readonly location: UserLocation | null; readonly ai: Readonly<Record<AiKind, AiPreference | null>>;
};
export type CallerKey = {
  readonly id: string; readonly name: string; readonly permission: 'read' | 'write';
  readonly created_at: number; readonly last_used_at: number | null;
};
export type ShortLink = { readonly code: string; readonly url: string; readonly expires_at: number | null };

export class SettingsInputError extends Error {
  readonly field: string;
  constructor(field: string, message: string) { super(message); this.field = field; this.name = 'SettingsInputError'; }
}
class SettingsDataError extends Error {
  readonly field: string;
  constructor(field: string) { super(`设置数据格式无效（${field}）`); this.field = field; this.name = 'SettingsDataError'; }
}

function preference(value: unknown): AiPreference | null {
  if (value === null || value === undefined) return null;
  const row = channelDocument(value);
  if (typeof row.default_model !== 'string') throw new SettingsDataError('default_model');
  return { default_model: row.default_model, options: channelDocument(row.options) };
}
function location(value: unknown): UserLocation | null {
  if (value === null || value === undefined) return null;
  const row = channelDocument(value);
  if (typeof row.lat !== 'number' || typeof row.lng !== 'number' || !Number.isFinite(row.lat) || !Number.isFinite(row.lng)) throw new SettingsDataError('location');
  return { lat: row.lat, lng: row.lng, country: channelText(row.country), country_code: channelText(row.country_code),
    city: channelText(row.city), city_code: channelText(row.city_code), district: channelText(row.district), district_code: channelText(row.district_code) };
}
function profile(value: unknown): LocalProfile {
  const row = channelDocument(value);
  if (typeof row.persona !== 'string' || typeof row.name !== 'string' || typeof row.avatar !== 'string'
    || (row.locale !== 'zh-CN' && row.locale !== 'en-US') || (row.user_type !== 'creator' && row.user_type !== 'business_owner')) throw new SettingsDataError('profile');
  const ai = channelDocument(row.ai);
  return { persona: row.persona, name: row.name, avatar: row.avatar, locale: row.locale, user_type: row.user_type,
    location: location(row.location), ai: { image: preference(ai.image), edit: preference(ai.edit), video: preference(ai.video), agent: preference(ai.agent) } };
}
function callerKey(value: unknown): CallerKey {
  const row = channelDocument(value);
  if (typeof row.id !== 'string' || typeof row.name !== 'string' || (row.permission !== 'read' && row.permission !== 'write')
    || typeof row.created_at !== 'number') throw new SettingsDataError('api_key');
  return { id: row.id, name: row.name, permission: row.permission, created_at: row.created_at,
    last_used_at: typeof row.last_used_at === 'number' ? row.last_used_at : null };
}
const query = (persona: string) => `?${new URLSearchParams({ persona })}`;

export async function loadLocalProfile(persona: string, signal: AbortSignal): Promise<LocalProfile> {
  return profile(await channelRequest('/api/local-user' + query(persona), { signal }));
}
export async function saveLocalProfile(persona: string, value: ChannelDocument, signal: AbortSignal): Promise<LocalProfile> {
  return profile(await channelWrite('/api/local-user' + query(persona), value, 'PUT', signal));
}
export async function saveAiPreferences(persona: string, values: Readonly<Record<AiKind, AiPreference>>, signal: AbortSignal): Promise<LocalProfile> {
  return profile(await channelWrite('/api/local-user/ai' + query(persona), values, 'PUT', signal));
}
export async function saveAiPreference(persona: string, kind: AiKind, value: AiPreference, signal: AbortSignal): Promise<LocalProfile> {
  return profile(await channelWrite('/api/local-user/ai/' + kind + query(persona), value, 'PUT', signal));
}
export async function loadCallerKeys(persona: string, signal: AbortSignal): Promise<readonly CallerKey[]> {
  return channelRows(await channelRequest('/api/access-keys' + query(persona), { signal })).map(callerKey);
}
export async function createCallerKey(persona: string, name: string, permission: 'read' | 'write', signal: AbortSignal): Promise<{ readonly entry: CallerKey; readonly secret: string }> {
  const result = channelDocument(await channelWrite('/api/access-keys', { persona, name, permission }, 'POST', signal));
  if (typeof result.key !== 'string' || !result.key.startsWith('eak_')) throw new SettingsDataError('key');
  return { entry: callerKey(result), secret: result.key };
}
export async function revokeCallerKey(persona: string, id: string, signal: AbortSignal): Promise<void> {
  await channelRequest('/api/access-keys/' + encodeURIComponent(id) + query(persona), { method: 'DELETE', signal });
}
export async function loadShortLinks(persona: string, signal: AbortSignal): Promise<readonly ShortLink[]> {
  return channelRows(await channelRequest('/api/short-links' + query(persona), { signal })).map((row) => {
    const url = channelUrl(row.url);
    if (typeof row.code !== 'string' || !url) throw new SettingsDataError('short_link');
    return { code: row.code, url, expires_at: typeof row.expires_at === 'number' ? row.expires_at : null };
  });
}
export async function createShortLink(persona: string, url: string, expiresAt: number | null, signal: AbortSignal): Promise<void> {
  await channelWrite('/api/short-links', { persona, url, expires_at: expiresAt }, 'POST', signal);
}
export async function deleteShortLink(persona: string, code: string, signal: AbortSignal): Promise<void> {
  await channelRequest('/api/short-links/' + encodeURIComponent(code) + query(persona), { method: 'DELETE', signal });
}
export function shortLinkUrl(code: string): string {
  const base = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '');
  return new URL(`${base}/s/${encodeURIComponent(code)}`, window.location.origin).href;
}
export function parsePreference(model: string, options: string): AiPreference {
  if (!model.trim()) throw new SettingsInputError('model', '请填写默认模型名称');
  let value: unknown;
  try { value = JSON.parse(options || '{}'); }
  catch (error: unknown) {
    if (error instanceof SyntaxError) throw new SettingsInputError('options', '高级参数需要有效的 JSON 对象');
    throw error;
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new SettingsInputError('options', '高级参数需要 JSON 对象');
  return { default_model: model.trim(), options: channelDocument(value) };
}
