import { useEffect, useRef, useState } from 'react';
import { CHANNEL_NAMES, channelDocument, channelError, channelRequest, channelText, channelUrl, channelWrite } from '../lib/channelsApi';
import type { ChannelDocument } from '../lib/channelsApi';
import { handoffActionUrl, handoffMediaUrl, readPublicHandoff } from '../lib/nativeHandoffApi';
import type { PublicHandoff } from '../lib/nativeHandoffApi';
import { useChannelJob } from './channels/useChannelJob';
import { RednoteGuestActions } from './channels/RednoteGuestActions';

export function ChannelHandoffGuest({ templateId, token }: { readonly templateId: string; readonly token: string }) {
  const [content, setContent] = useState<PublicHandoff | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [result, setResult] = useState<ChannelDocument | null>(null);
  const [message, setMessage] = useState('');
  const [statusError, setStatusError] = useState('');
  const [reload, setReload] = useState(0);
  const requestKey = useRef(crypto.randomUUID());
  const { busy, error, perform } = useChannelJob();
  const path = `/api/native-handoff/templates/${encodeURIComponent(templateId)}`;
  const query = new URLSearchParams({ token }).toString();
  const runId = channelText(result?.run_id);
  const state = channelText(result?.state);
  const valid = /^[a-f0-9]{32}$/.test(templateId) && /^[a-f0-9]{64}$/.test(token);
  useEffect(() => {
    if (!valid) return;
    void perform(async (signal) => readPublicHandoff(await channelRequest(`${path}?${query}`, { signal })), setContent);
  }, [path, query, valid, reload, perform]);
  useEffect(() => {
    if (!content || !runId || state !== 'submitted') return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (controller.signal.aborted) return;
      try {
        const value = channelDocument(await channelRequest(`${path}/runs/${encodeURIComponent(runId)}?${query}`, { signal: controller.signal }));
        if (controller.signal.aborted) return;
        setResult(value); setStatusError('');
        if (value.state !== 'submitted') return;
      } catch (failure: unknown) { if (controller.signal.aborted) return; setStatusError(channelError(failure)); }
      if (content && Date.now() / 1000 < content.expires_at) timer = setTimeout(() => void poll(), 3000);
    }
    timer = setTimeout(() => void poll(), 3000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [content, runId, state, path, query]);
  const action = result?.user_action ? channelDocument(result.user_action) : {};
  const actionUrl = handoffActionUrl(action.scheme || action.short_link);
  const liveAction = !action.expires_at || Number(action.expires_at) > Date.now() / 1000;
  return <main className="accounts-page platform-workbench" style={{ maxWidth: '48rem', marginInline: 'auto', width: '100%' }}>
    <h1 className="page-title">应用分享</h1>
    {!valid ? <p className="panel" role="alert">分享链接无效，请向创建者获取完整链接。</p> : <>
      {!content && !error && <p role="status">正在读取分享内容…</p>}
      {content && <section className="panel">
        <h2 className="panel-title">{CHANNEL_NAMES[content.platform]} · {content.title || '待分享内容'}</h2>
        <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{content.body}</p>
        <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async () => {
          if (!navigator.clipboard) throw new Error('当前浏览器不支持复制，请选中文案复制');
          await navigator.clipboard.writeText([content.title, content.body].filter(Boolean).join('\n\n'));
        }, () => setMessage('文案已复制'))}>复制标题与正文</button>
        {content.media.map((item, index) => {
          const url = handoffMediaUrl(item.path, templateId, token);
          return url && <figure key={url} style={{ margin: 'var(--space-4, 16px) 0', maxWidth: '100%' }}>
            {item.kind === 'video' ? <video controls preload="metadata" src={url} style={{ width: '100%', maxHeight: '28rem' }} />
              : <img src={url} alt={`分享图片 ${index + 1}`} style={{ display: 'block', maxWidth: '100%', maxHeight: '28rem', objectFit: 'contain' }} />}
            <figcaption><a href={url} download>下载媒体 {index + 1}</a></figcaption>
          </figure>;
        })}
        <p className="pv-hint">链接到期时间 {new Date(content.expires_at * 1000).toLocaleString()}</p>
        <p className="pv-hint">发布账号将在平台应用中选择。</p>
        {!runId && <><label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 已审核以上内容与媒体，确认在平台应用中继续</label>
          <button className="btn btn-primary" disabled={busy || !reviewed || content.expires_at <= Date.now() / 1000} onClick={() => void perform(async (signal) => channelDocument(await channelWrite(`${path}/start?${query}`, { request_key: requestKey.current, reviewed: true }, 'POST', signal)),
            (value) => { setResult(value); setReviewed(false); setMessage(''); })}>准备应用分享</button></>}
        {runId && <p role="status">{state === 'published' ? '平台已确认发布' : state === 'submitted' ? '等待在应用中完成发布，正在核验平台结果' : `平台状态 ${state}`}</p>}
        {!!result?.platform_work_id && <p className="pv-hint" style={{ overflowWrap: 'anywhere' }}>平台作品 ID {channelText(result.platform_work_id)}</p>}
        {channelUrl(result?.permalink) && <p><a href={channelUrl(result?.permalink)} target="_blank" rel="noreferrer">查看已确认的平台作品</a></p>}
        {actionUrl && liveAction && <a className="btn btn-primary" href={actionUrl}>打开抖音 App</a>}
        {!!action.expires_at && <p className="pv-hint">应用接续到期时间 {new Date(Number(action.expires_at) * 1000).toLocaleString()}</p>}
        {result?.verifyConfig !== undefined && content.platform === 'rednote-link' && <RednoteGuestActions content={content} result={result} token={token} />}
      </section>}
      {(error || statusError) && <p className="panel" role="alert">{error || statusError} <button className="btn btn-sm" onClick={() => setReload((value) => value + 1)}>重新读取分享内容</button></p>}
      {message && <p role="status">{message}</p>}
    </>}
  </main>;
}
