import { useState } from 'react';
import { channelDocument, channelRows, channelText, channelWrite, readChannelApproval } from '../../lib/channelsApi';
import type { ChannelApproval, ChannelDocument, ChannelScope } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';

function previewUrl(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/api/channel-publish/media/')) return '';
  const base = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '');
  return `${base}${value}`;
}

export function ChannelReapproval({ platform, runId, scope, onChanged }: {
  readonly platform: string; readonly runId: string; readonly scope: ChannelScope; readonly onChanged: (value: unknown) => void;
}) {
  const [original, setOriginal] = useState<ChannelDocument | null>(null);
  const [media, setMedia] = useState<readonly ChannelDocument[]>([]);
  const [approval, setApproval] = useState<ChannelApproval | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const { busy, error, perform } = useChannelJob();
  const path = `/api/channel-publish/${encodeURIComponent(platform)}/${encodeURIComponent(runId)}/reapprove`;
  return <details><summary>重新审核冻结发布计划或已提交任务</summary>
    <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => channelDocument(await channelWrite(`${path}/precheck`, scope, 'POST', signal)), (value) => {
      setOriginal(channelDocument(value.original_intent)); setMedia(channelRows(value.frozen_media)); setApproval(readChannelApproval(value)); setReviewed(false);
    })}>读取冻结任务的内容与媒体</button>
    {original && <><h4>{channelText(original.title)}</h4><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{channelText(original.body)}</p>
      <p className="pv-hint" style={{ overflowWrap: 'anywhere' }}>画像 {scope.persona} · 平台账号 {scope.account_uid}</p>
      {!!original.scheduled_for && <p className="pv-hint">原计划发布时间 {new Date(channelText(original.scheduled_for)).toLocaleString()}</p>}
      {media.map((item, index) => <figure key={channelText(item.sha256) || index} style={{ margin: 'var(--space-3, 12px) 0', maxWidth: '100%' }}>
        {previewUrl(item.preview_url) && (item.kind === 'video' ? <video controls preload="metadata" src={previewUrl(item.preview_url)} style={{ maxWidth: '100%', maxHeight: '18rem' }} />
          : <img src={previewUrl(item.preview_url)} alt={item.role === 'cover' ? '审核封面' : `审核媒体 ${index + 1}`} style={{ maxWidth: '100%', maxHeight: '18rem' }} />)}
        <figcaption className="pv-hint" style={{ overflowWrap: 'anywhere' }}>{item.role === 'cover' ? '封面' : '发布媒体'} · {channelText(item.size)} 字节</figcaption>
      </figure>)}
      <details><summary>原始平台选项</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(original.options, null, 2)}</pre></details>
      <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 已审核此账号的原始内容和以上媒体，确认继续处理</label>
      <button className="btn" disabled={busy || !reviewed || !approval} onClick={() => void perform(
        (signal) => channelWrite(path, { ...scope, ...approval, reviewed: true }, 'POST', signal),
        (value) => { setReviewed(false); setApproval(null); onChanged(value); },
      )}>确认审核并继续</button>
    </>}
    {error && <p role="alert">{error}</p>}
  </details>;
}
