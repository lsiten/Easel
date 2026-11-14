import { useEffect, useState } from 'react';
import { channelDocument, channelRequest, channelRows, channelText, channelUrl } from '../../lib/channelsApi';
import type { ChannelDocument, ChannelPlatform } from '../../lib/channelsApi';
import { NativeHandoffEditor } from './NativeHandoffEditor';
import { useChannelJob } from './useChannelJob';
import { ownerHandoffUrl } from '../../lib/nativeHandoffApi';

export function ChannelNativeHandoff({ persona, spec }: { readonly persona: string; readonly spec: ChannelPlatform }) {
  const [templates, setTemplates] = useState<readonly ChannelDocument[]>([]);
  const [selected, setSelected] = useState<ChannelDocument | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [reload, setReload] = useState(0);
  const { busy, error, perform } = useChannelJob();
  const base = `/api/native-handoff/${encodeURIComponent(spec.platform)}/templates`;
  const query = new URLSearchParams({ persona }).toString();
  useEffect(() => {
    void perform(async (signal) => channelRows(await channelRequest(`${base}?${query}`, { signal })), setTemplates);
  }, [base, query, reload, perform]);
  return <>
    <NativeHandoffEditor persona={persona} spec={spec} onCreated={(template) => { setSelected(template); setReviewed(false); setReload((value) => value + 1); }} />
    <section className="panel" aria-label="应用分享模板管理">
      <h2 className="panel-title">应用分享模板</h2>
      <button className="btn btn-sm" disabled={busy} onClick={() => setReload((value) => value + 1)}>刷新分享模板</button>
      {!busy && !templates.length && <p className="pv-hint">当前画像暂无分享模板。</p>}
      {templates.map((template) => <article key={channelText(template.template_id)} style={{ paddingBlock: 'var(--space-3, 12px)', borderBottom: '1px solid var(--border)', overflowWrap: 'anywhere' }}>
        <p>{channelText(template.template_id)} · {channelText(template.state)}</p>
        <p className="pv-hint">{Number(template.expires_at) <= Date.now() / 1000 ? '已到期' : `到期时间 ${new Date(Number(template.expires_at) * 1000).toLocaleString()}`}</p>
        <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => channelDocument(await channelRequest(`${base}/${encodeURIComponent(channelText(template.template_id))}?${query}`, { signal })),
          (value) => { setSelected(value); setReviewed(false); })}>查看与管理分享链接</button>
      </article>)}
      {selected && <section style={{ marginTop: 'var(--space-4, 16px)', overflowWrap: 'anywhere' }} aria-label="所选分享模板">
        <h3 className="panel-title">分享链接</h3><p className="pv-hint">模板 {channelText(selected.template_id)} · {channelText(selected.state)}</p>
        {selected.state === 'ready_for_manual_submission' && Number(selected.expires_at) > Date.now() / 1000 && ownerHandoffUrl(selected) && <>
          <a href={ownerHandoffUrl(selected)} target="_blank" rel="noreferrer">打开限时分享页面，在应用中继续</a>
          {channelUrl(selected.public_url) ? <img className="qr-img" style={{ display: 'block', maxWidth: '100%', marginTop: 'var(--space-3, 12px)' }}
            src={`${window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '')}/api/native-handoff/templates/${encodeURIComponent(channelText(selected.template_id))}/qr?${new URLSearchParams({ token: channelText(selected.token) })}`} alt="原生应用分享页面二维码" />
            : <p className="pv-hint">公网分享需在 .env 中配置 EASEL_CHANNEL_MEDIA_BASE_URL。</p>}
          <p className="pv-hint">复制此链接交给需要发布的人。访问者会在分享页面核对内容并选择应用账号。</p>
        </>}
        <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 确认撤销此模板的分享权限</label>
        <button className="btn btn-danger" disabled={busy || !reviewed || selected.state !== 'ready_for_manual_submission'} onClick={() => void perform(
          (signal) => channelRequest(`${base}/${encodeURIComponent(channelText(selected.template_id))}?${query}`, { method: 'DELETE', signal }),
          () => { setSelected(null); setReviewed(false); setReload((value) => value + 1); },
        )}>撤销分享模板</button>
      </section>}
      {error && <p role="alert">{error}</p>}
    </section>
  </>;
}
