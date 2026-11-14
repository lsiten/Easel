import { document, rows, text } from './aiWorkspaceApi';

const FALLBACK_LABELS: Readonly<Record<string, string>> = {
  VOLCENGINE_ACCESS_KEY_ID: '火山引擎访问密钥 ID',
  VOLCENGINE_SECRET_ACCESS_KEY: '火山引擎访问密钥',
  VOLCENGINE_SPACE_NAME: '视频点播空间名称',
  VOLCENGINE_VOD_BASE_URL: '视频点播服务地址',
  VOLCENGINE_REGION: '火山引擎区域',
  VOLCENGINE_PLAYBACK_BASE_URL: '视频播放服务地址',
  VOLCENGINE_URL_AUTH_PRIMARY_KEY: '视频播放签名密钥',
  EASEL_DRAFT_ASR_MODEL: '本地语音识别模型',
  EASEL_DRAFT_MODEL_RATES: '模型费率 JSON',
  EASEL_CHANNEL_MEDIA_BASE_URL: '公开媒体 HTTPS 地址',
};
export type RuntimeSetting = {
  readonly name: string; readonly label: string; readonly secret: boolean; readonly configured: boolean;
  readonly locked: boolean; readonly value: string; readonly description: string; readonly required: boolean;
  readonly default: string; readonly section: string;
};
export function runtimeSettings(value: unknown, allowed: readonly string[] = Object.keys(FALLBACK_LABELS)): readonly RuntimeSetting[] {
  const permitted = new Set(allowed);
  return rows(document(value).fields).filter((field) => typeof field.name === 'string' && permitted.has(field.name)).map((field) => {
    if (typeof field.name !== 'string' || typeof field.label !== 'string' || typeof field.value !== 'string'
      || typeof field.secret !== 'boolean' || typeof field.configured !== 'boolean'
      || typeof field.locked !== 'boolean') throw new Error('运行选项返回格式无效');
    return { name: field.name, label: FALLBACK_LABELS[field.name] || text(field.label), secret: field.secret,
      configured: field.configured,
      locked: field.locked, value: field.secret ? '' : field.value,
      description: text(field.description),
      required: field.required === true,
      default: text(field.default),
      section: text(field.section) || '通道参数' };
  });
}
export function runtimeSettingsUpdate(fields: readonly RuntimeSetting[], edits: Readonly<Record<string, string>>) {
  const values: Record<string, string> = {};
  for (const field of fields) {
    const value = edits[field.name];
    if (field.locked || value === undefined || (field.secret && !value.trim()) || value === field.value) continue;
    if (field.name === 'EASEL_DRAFT_MODEL_RATES' && value.trim()) {
      const rates: unknown = JSON.parse(value);
      if (rates === null || Array.isArray(rates) || typeof rates !== 'object') throw new Error('费率必须是按模型名称分组的 JSON 对象');
    }
    values[field.name] = value;
  }
  return { values };
}
