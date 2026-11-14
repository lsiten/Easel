import { useCallback, useEffect, useState } from 'react';
import { AI_KINDS, AI_LABELS, SettingsInputError, loadLocalProfile, parsePreference,
  saveAiPreference, saveAiPreferences, saveLocalProfile } from '../lib/channelSettingsApi';
import type { AiKind, AiPreference, LocalProfile } from '../lib/channelSettingsApi';
import type { ChannelDocument } from '../lib/channelsApi';
import { useChannelJob } from './channels/useChannelJob';

const LOCATION_FIELDS = [
  ['lat', '纬度'], ['lng', '经度'], ['country', '国家或地区'], ['country_code', '国家或地区代码'],
  ['city', '城市'], ['city_code', '城市代码'], ['district', '区县'], ['district_code', '区县代码'],
] as const;
type LocationField = typeof LOCATION_FIELDS[number][0];
type ProfileDraft = {
  readonly name: string; readonly avatar: string; readonly locale: 'zh-CN' | 'en-US';
  readonly user_type: 'creator' | 'business_owner'; readonly location: Readonly<Record<LocationField, string>>;
};
type AiDraft = { readonly model: string; readonly options: string };
type AiDrafts = Readonly<Record<AiKind, AiDraft>>;
const GRID = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(16rem, 100%), 1fr))', gap: 'var(--space-3, 12px)' } as const;

function profileDraft(profile: LocalProfile): ProfileDraft {
  const place = profile.location;
  return { name: profile.name, avatar: profile.avatar, locale: profile.locale, user_type: profile.user_type,
    location: { lat: place ? String(place.lat) : '', lng: place ? String(place.lng) : '', country: place?.country || '',
      country_code: place?.country_code || '', city: place?.city || '', city_code: place?.city_code || '',
      district: place?.district || '', district_code: place?.district_code || '' } };
}
function aiDraft(value: AiPreference | null): AiDraft {
  return { model: value?.default_model || '', options: JSON.stringify(value?.options || {}, null, 2) };
}
function aiDrafts(profile: LocalProfile): AiDrafts {
  return { image: aiDraft(profile.ai.image), edit: aiDraft(profile.ai.edit), video: aiDraft(profile.ai.video), agent: aiDraft(profile.ai.agent) };
}
function profilePayload(draft: ProfileDraft): ChannelDocument {
  const { lat, lng, ...place } = draft.location;
  const result = { name: draft.name.trim(), avatar: draft.avatar.trim(), locale: draft.locale, user_type: draft.user_type };
  if (!lat.trim() && !lng.trim() && Object.values(place).every((value) => !value.trim())) return result;
  const latitude = Number(lat), longitude = Number(lng);
  if (!lat.trim() || !lng.trim() || !Number.isFinite(latitude) || !Number.isFinite(longitude)
    || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) throw new SettingsInputError('location', '请同时填写有效的纬度和经度');
  return { ...result, location: { ...place, lat: latitude, lng: longitude } };
}

export function ChannelProfileSettings({ persona }: { readonly persona: string }) {
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [models, setModels] = useState<AiDrafts | null>(null);
  const [message, setMessage] = useState('');
  const [section, setSection] = useState<'profile' | 'ai'>('profile');
  const { busy, error, perform } = useChannelJob();
  const load = useCallback(() => {
    void perform((signal) => loadLocalProfile(persona, signal), (value) => {
      setDraft(profileDraft(value)); setModels(aiDrafts(value)); setMessage('');
    });
  }, [persona, perform]);
  useEffect(load, [load]);
  if (!draft || !models) return <section className="panel" aria-label="画像设置">
    <h2 className="panel-title">画像设置</h2>
    {error ? <p role="alert">{error}</p> : <p role="status">正在加载画像设置…</p>}
    <button className="btn" disabled={busy} onClick={load}>重新加载</button>
  </section>;
  function saveModel(kind: AiKind) {
    if (!models) return;
    setSection('ai');
    void perform((signal) => saveAiPreference(persona, kind, parsePreference(models[kind].model, models[kind].options), signal), (value) => {
      setModels((current) => current ? { ...current, [kind]: aiDraft(value.ai[kind]) } : current);
      setMessage(`${AI_LABELS[kind]}模型已保存`);
    });
  }
  const feedback = <>{error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {busy && <p className="pv-hint" role="status">正在保存设置…</p>}</>;
  return <>
    <section className="panel" aria-label="画像资料">
      <h2 className="panel-title">画像资料</h2>
      <form onSubmit={(event) => { event.preventDefault(); setMessage(''); setSection('profile');
        void perform((signal) => saveLocalProfile(persona, profilePayload(draft), signal), (value) => {
          setDraft(profileDraft(value)); setMessage('画像资料已保存');
        }); }}>
        <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <div style={GRID}>
            <label className="field-label">显示名称<input className="field" required maxLength={200} value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
            <label className="field-label">头像地址或媒体路径<input className="field" maxLength={2048} value={draft.avatar}
              onChange={(event) => setDraft({ ...draft, avatar: event.target.value })} /></label>
            <label className="field-label">语言偏好<select className="field" value={draft.locale}
              onChange={(event) => setDraft({ ...draft, locale: event.target.value === 'en-US' ? 'en-US' : 'zh-CN' })}>
              <option value="zh-CN">简体中文</option><option value="en-US">English</option></select></label>
            <label className="field-label">用户类型<select className="field" value={draft.user_type}
              onChange={(event) => setDraft({ ...draft, user_type: event.target.value === 'business_owner' ? 'business_owner' : 'creator' })}>
              <option value="creator">内容创作者</option><option value="business_owner">企业经营者</option></select></label>
          </div>
          <p className="pv-hint">头像可使用 HTTPS 地址或当前画像拥有的媒体路径。</p>
          <details><summary>位置资料</summary><div style={GRID}>
            {LOCATION_FIELDS.map(([field, label]) => <label className="field-label" key={field}>{label}
              <input className="field" type={field === 'lat' || field === 'lng' ? 'number' : 'text'} step="any"
                maxLength={field.endsWith('code') ? 30 : 200} value={draft.location[field]}
                onChange={(event) => setDraft({ ...draft, location: { ...draft.location, [field]: event.target.value } })} />
            </label>)}
          </div></details>
          <button className="btn btn-primary" type="submit" style={{ marginTop: 'var(--space-4, 16px)' }}>保存画像资料</button>
        </fieldset>
      </form>
      {section === 'profile' && feedback}
    </section>
    <section className="panel" aria-label="AI 默认模型">
      <h2 className="panel-title">AI 默认模型</h2>
      {AI_KINDS.map((kind) => <form key={kind} onSubmit={(event) => { event.preventDefault(); setMessage(''); saveModel(kind); }}
        style={{ marginBottom: 'var(--space-5, 20px)' }}>
        <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <label className="field-label">{AI_LABELS[kind]}默认模型<input className="field" maxLength={200} value={models[kind].model}
            onChange={(event) => setModels({ ...models, [kind]: { ...models[kind], model: event.target.value } })} /></label>
          <details><summary>{AI_LABELS[kind]}高级参数 JSON</summary>
            <textarea className="field" rows={4} spellCheck={false} aria-label={`${AI_LABELS[kind]}高级参数 JSON`} value={models[kind].options}
              onChange={(event) => setModels({ ...models, [kind]: { ...models[kind], options: event.target.value } })} />
          </details>
          <button className="btn" type="submit" disabled={!models[kind].model.trim()} style={{ marginTop: 'var(--space-2, 8px)' }}>保存{AI_LABELS[kind]}模型</button>
        </fieldset>
      </form>)}
      <button className="btn" disabled={busy || AI_KINDS.some((kind) => !models[kind].model.trim())} onClick={() => {
        setMessage(''); setSection('ai');
        void perform((signal) => saveAiPreferences(persona, {
          image: parsePreference(models.image.model, models.image.options), edit: parsePreference(models.edit.model, models.edit.options),
          video: parsePreference(models.video.model, models.video.options), agent: parsePreference(models.agent.model, models.agent.options),
        }, signal), (value) => { setModels(aiDrafts(value)); setMessage('四类默认模型已保存'); });
      }}>统一保存四类模型</button>
      {section === 'ai' && feedback}
    </section>
  </>;
}
