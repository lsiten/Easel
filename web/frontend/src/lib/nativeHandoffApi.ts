import { channelDocument, channelRows, channelText, channelUrl } from './channelsApi.ts';
import type { ChannelDocument } from './channelsApi.ts';

export type PublicHandoff = {
  readonly template_id: string; readonly platform: 'douyin-open' | 'rednote-link'; readonly title: string;
  readonly body: string; readonly expires_at: number; readonly media: readonly ChannelDocument[];
};
export type RednoteSdk = { readonly share: (options: ChannelDocument) => unknown };
export const REDNOTE_SDK_SOURCE = 'https://fe-static.xhscdn.com/biz-static/goten/xhs-1.0.1.js';
let loadingSdk: Promise<RednoteSdk> | null = null;

export function ownerHandoffUrl(template: ChannelDocument): string {
  const path = template.path;
  if (typeof path !== 'string' || !path.startsWith('/?')) return '';
  const query = new URLSearchParams(path.slice(2));
  function matches(parameters: URLSearchParams) {
    return [...parameters.keys()].length === 2 && parameters.get('handoff') === template.template_id && parameters.get('token') === template.token;
  }
  if (!matches(query)) return '';
  const publicUrl = channelUrl(template.public_url);
  if (publicUrl) {
    const parsed = new URL(publicUrl);
    if (matches(parsed.searchParams) && !parsed.hash) return publicUrl;
  }
  const base = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '');
  return `${base}${path}`;
}

export function handoffActionUrl(value: unknown): string {
  if (typeof value !== 'string') return '';
  try {
    const url = new URL(value);
    if (url.protocol === 'snssdk1128:' && url.hostname === 'openplatform' && url.pathname === '/share' && !url.username && !url.password && !url.port) return value;
    if (url.protocol === 'https:' && ['open.douyin.com', 'channels.weixin.qq.com', 'www.xiaohongshu.com', 'creator.xiaohongshu.com'].includes(url.hostname) && !url.username && !url.password && (!url.port || url.port === '443')) return url.href;
    return '';
  } catch (error: unknown) { if (error instanceof TypeError) return ''; throw error; }
}
export function handoffMediaUrl(path: unknown, templateId: string, token: string): string {
  if (typeof path !== 'string' || !path.startsWith(`/api/native-handoff/templates/${templateId}/media/`)) return '';
  const url = new URL(path, window.location.origin);
  const index = url.pathname.slice(`/api/native-handoff/templates/${templateId}/media/`.length);
  if (!/^\d+$/.test(index) || url.searchParams.get('token') !== token) return '';
  const base = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '');
  return new URL(`${base}${path}`, window.location.origin).href;
}
export function readPublicHandoff(value: unknown): PublicHandoff {
  const row = channelDocument(value);
  if (typeof row.template_id !== 'string' || (row.platform !== 'douyin-open' && row.platform !== 'rednote-link') || typeof row.expires_at !== 'number') throw new Error('分享模板数据无效');
  return { template_id: row.template_id, platform: row.platform, title: channelText(row.title), body: channelText(row.body), expires_at: row.expires_at, media: channelRows(row.media) };
}
function isSdk(value: unknown): value is RednoteSdk {
  return value !== null && typeof value === 'object' && typeof Reflect.get(value, 'share') === 'function';
}
export function getRednoteSdk(): RednoteSdk | null {
  const value: unknown = Reflect.get(window, 'xhs');
  return isSdk(value) ? value : null;
}
export function loadRednoteSdk(): Promise<RednoteSdk> {
  const available = getRednoteSdk();
  if (available) return Promise.resolve(available);
  if (loadingSdk) return loadingSdk;
  const pending = new Promise<RednoteSdk>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = REDNOTE_SDK_SOURCE; script.async = true; script.referrerPolicy = 'no-referrer';
    const timeout = setTimeout(() => { script.remove(); reject(new Error('官方分享 SDK 加载超时，可复制文案并下载媒体继续')); }, 15000);
    script.onload = () => {
      clearTimeout(timeout);
      const sdk = getRednoteSdk();
      if (sdk) resolve(sdk);
      else { script.remove(); reject(new Error('官方 SDK 未提供分享接口')); }
    };
    script.onerror = () => { clearTimeout(timeout); script.remove(); reject(new Error('官方分享 SDK 加载失败，可复制文案并下载媒体继续')); };
    document.head.append(script);
  });
  loadingSdk = pending;
  void pending.then(() => { if (loadingSdk === pending) loadingSdk = null; }, () => { if (loadingSdk === pending) loadingSdk = null; });
  return pending;
}
export function rednoteShareRequest(config: unknown, media: readonly ChannelDocument[], templateId: string, token: string): ChannelDocument {
  const verification = channelDocument(config);
  if (['appKey', 'nonce', 'timestamp', 'signature'].some((key) => !channelText(verification[key]))) throw new Error('小红书分享校验参数无效');
  const urls = media.map((item) => handoffMediaUrl(item.path, templateId, token));
  if (!urls.length || urls.some((url) => !url)) throw new Error('分享媒体地址无效');
  if (media[0]?.kind === 'video') {
    if (media.length !== 1) throw new Error('视频分享需要单个视频');
    return { verifyConfig: verification, shareInfo: { type: 'video', video: urls[0] } };
  }
  if (media.some((item) => item.kind !== 'image')) throw new Error('图文分享需要图片媒体');
  return { verifyConfig: verification, shareInfo: { type: 'normal', images: urls } };
}
