import { useRef, useState } from 'react';
import { channelDocument, channelJson, channelRows, channelText, channelWrite } from '../../lib/channelsApi';
import type { ChannelDocument, ChannelPlatform } from '../../lib/channelsApi';
import { ChannelMaterialPicker } from './ChannelMaterialPicker';
import { OwnedMediaPicker } from './OwnedMediaPicker';
import { useChannelJob } from './useChannelJob';

export function NativeHandoffEditor({ persona, spec, onCreated }: {
  readonly persona: string; readonly spec: ChannelPlatform; readonly onCreated: (template: ChannelDocument) => void;
}) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [kind, setKind] = useState('image');
  const [candidate, setCandidate] = useState('');
  const [paths, setPaths] = useState<readonly string[]>([]);
  const [material, setMaterial] = useState<ChannelDocument | null>(null);
  const [options, setOptions] = useState('{}');
  const [expiry, setExpiry] = useState('3600');
  const [reviewed, setReviewed] = useState(false);
  const [message, setMessage] = useState('');
  const requestKey = useRef(crypto.randomUUID());
  const { busy, error, perform } = useChannelJob();
  const maximum = kind === 'video' ? 1 : typeof spec.limits.max_images === 'number' ? spec.limits.max_images : 9;
  function changed() { setReviewed(false); setMessage(''); requestKey.current = crypto.randomUUID(); }
  return <section className="panel" aria-label="原生应用分享模板">
    <h2 className="panel-title">创建应用分享模板</h2>
    <ChannelMaterialPicker persona={persona} disabled={busy} onSelect={(record) => {
      setMaterial(record); changed();
      if (!record) return;
      const media = channelRows(record.media).filter((item) => item.kind === 'image' || item.kind === 'video');
      const selectedKind = media[0]?.kind === 'video' ? 'video' : 'image';
      setTitle(channelText(record.title)); setBody(channelText(record.description)); setKind(selectedKind);
      setPaths(media.filter((item) => item.kind === selectedKind).map((item) => channelText(item.path)));
    }} />
    {material && <p className="pv-hint">关联草稿 {channelText(material.title)}</p>}
    <label className="field-label" htmlFor="handoff-title">标题</label><input id="handoff-title" className="field" value={title} disabled={busy} onChange={(event) => { setTitle(event.target.value); changed(); }} />
    <label className="field-label" htmlFor="handoff-body">正文</label><textarea id="handoff-body" className="field" value={body} disabled={busy} onChange={(event) => { setBody(event.target.value); changed(); }} />
    <label className="field-label" htmlFor="handoff-kind">媒体类型</label><select id="handoff-kind" className="field" value={kind} disabled={busy} onChange={(event) => { setKind(event.target.value); setPaths([]); setCandidate(''); changed(); }}><option value="image">图片</option><option value="video">视频</option></select>
    <OwnedMediaPicker persona={persona} kind={kind} value={candidate} disabled={busy} onChange={setCandidate} />
    <button className="btn btn-sm" disabled={busy || !candidate || paths.includes(candidate) || paths.length >= maximum} onClick={() => { setPaths((old) => [...old, candidate]); setCandidate(''); changed(); }}>添加所选媒体</button>
    <p className="pv-hint">已选 {paths.length} / {maximum} 个媒体</p>
    <ul style={{ paddingLeft: 'var(--space-4, 16px)' }}>{paths.map((path) => <li key={path} style={{ overflowWrap: 'anywhere', paddingBlock: 'var(--space-2, 8px)' }}>{path}{' '}
      <button className="btn btn-sm" disabled={busy} onClick={() => { setPaths((old) => old.filter((value) => value !== path)); changed(); }}>移除此媒体</button></li>)}</ul>
    <label className="field-label" htmlFor="handoff-expiry">分享链接有效秒数，60 至 86400</label><input id="handoff-expiry" className="field" type="number" min="60" max="86400" step="1" value={expiry} disabled={busy} onChange={(event) => { setExpiry(event.target.value); changed(); }} />
    {spec.platform === 'douyin-open' && <details><summary>应用分享选项</summary><label className="field-label" htmlFor="handoff-options">short_title、cover_tsp、download_type、private_status 选项 JSON</label>
      <textarea id="handoff-options" className="field" value={options} disabled={busy} onChange={(event) => { setOptions(event.target.value); changed(); }} /></details>}
    <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 已审核以上内容和媒体，确认创建可在原生应用接续的限时分享链接</label>
    <button className="btn btn-primary" disabled={busy || !reviewed || !paths.length || paths.length > maximum || !Number.isInteger(Number(expiry)) || Number(expiry) < 60 || Number(expiry) > 86400} onClick={() => void perform(async (signal) => channelDocument(await channelWrite(
      `/api/native-handoff/${encodeURIComponent(spec.platform)}/templates`, { persona, request_key: requestKey.current, title, body,
        media: paths.map((path) => ({ path, kind })), options: spec.platform === 'douyin-open' ? channelJson(options) : {},
        context: material ? { material_id: channelText(material.id), material_group_id: channelText(material.group_id),
          media_ids: channelRows(material.media).filter((item) => paths.includes(channelText(item.path))).map((item) => channelText(item.media_id)).filter(Boolean), source: 'native_handoff_workbench' } : {},
        reviewed: true, expires_in: Number(expiry) }, 'POST', signal,
    )), (template) => { setReviewed(false); setMessage('分享模板已创建，请在下方打开分享链接或二维码'); onCreated(template); })}>创建限时分享模板</button>
    {busy && <p role="status">正在校验并归档分享媒体…</p>}{message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
