import { useEffect, useState } from 'react';
import { CHANNEL_NAMES, channelDocument, channelRequest, channelText, channelUrl, channelWrite } from '../../lib/channelsApi';
import type { ChannelDocument } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';
import { ChannelReapproval } from './ChannelReapproval';
import { handoffActionUrl } from '../../lib/nativeHandoffApi';

export function ChannelRecordDetail({ persona, record, onChanged }: {
  readonly persona: string; readonly record: ChannelDocument; readonly onChanged: (record: ChannelDocument | null) => void;
}) {
  const [workId, setWorkId] = useState(channelText(record.platform_work_id));
  const [link, setLink] = useState(channelText(record.permalink));
  const [remove, setRemove] = useState(false);
  const [userAction, setUserAction] = useState<ChannelDocument | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const { busy, error, perform } = useChannelJob();
  const id = encodeURIComponent(channelText(record.run_id));
  const platform = encodeURIComponent(channelText(record.platform));
  const scope = { persona, account_uid: channelText(record.account_uid || record.account_id) };
  const query = new URLSearchParams({ persona });
  const official = Boolean(CHANNEL_NAMES[channelText(record.platform)]);
  const actionExpiry = typeof userAction?.expires_at === 'number' ? userAction.expires_at : 0;
  const actionExpired = actionExpiry > 0 && actionExpiry <= Math.max(now, Date.now() / 1000);
  const actionLink = actionExpired ? '' : handoffActionUrl(userAction?.scheme) || channelUrl(userAction?.short_link);
  useEffect(() => {
    if (!actionExpiry) return;
    const timer = setTimeout(() => setNow(Date.now() / 1000), Math.max(0, (actionExpiry - Date.now() / 1000) * 1000 + 10));
    return () => clearTimeout(timer);
  }, [actionExpiry]);
  return <section aria-label="发布记录详情" style={{ marginTop: 'var(--space-4, 16px)' }}>
    <h3 className="panel-title">发布记录详情</h3><p style={{ overflowWrap: 'anywhere' }}>{channelText(record.run_id)} · {channelText(record.state)}</p>
    <p className="pv-hint">作品链接 {channelText(record.work_link_status) || '—'}{record.cover_status ? ` · 封面 ${channelText(record.cover_status)}` : ''}{record.visibility ? ` · 可见性 ${channelText(record.visibility)}` : ''}</p>
    {!!(record.error || record.last_error || record.cover_error) && <p role="alert">{channelText(record.error || record.last_error || record.cover_error)}</p>}
    <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => channelDocument(await channelRequest(`/api/publish-records/${id}?${query}`, { signal })), onChanged)}>刷新记录</button>
    {official && ['submitted', 'running'].includes(channelText(record.state)) && <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => channelDocument(await channelWrite(
      `/api/channel-publish/${platform}/${id}/finalize`, scope, 'POST', signal,
    )), onChanged)}>继续平台确认</button>}
    {!official && ['submitted', 'running', 'unknown'].includes(channelText(record.state)) && <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => {
      await channelRequest(`/api/publish/${platform}/status?${new URLSearchParams({ persona, run_id: channelText(record.run_id) })}`, { signal });
      return channelDocument(await channelRequest(`/api/publish-records/${id}?${query}`, { signal }));
    }, onChanged)}>读取浏览器任务状态</button>}
    {record.state === 'submitted' && CHANNEL_NAMES[channelText(record.platform)] && <ChannelReapproval platform={channelText(record.platform)} runId={channelText(record.run_id)} scope={scope} onChanged={(value) => onChanged(channelDocument(value))} />}
    <details><summary>核验或标记作品链接</summary>
      <label className="field-label" htmlFor="record-work-id">平台作品 ID</label><input id="record-work-id" className="field" value={workId} onChange={(event) => setWorkId(event.target.value)} />
      <label className="field-label" htmlFor="record-work-link">作品链接</label><input id="record-work-link" className="field" type="url" value={link} onChange={(event) => setLink(event.target.value)} />
      <button className="btn" disabled={busy || (!workId && !link)} onClick={() => void perform(async (signal) => channelDocument(await channelWrite(
        `/api/publish-records/${id}/work-link`, { persona, status: 'ready', platform_work_id: workId, permalink: link }, 'PATCH', signal,
      )), onChanged)}>读取平台证据并核验</button>
      {[['pending', '标为待核验'], ['error', '标记链接异常']].map(([status, label]) => <button key={status} className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => channelDocument(await channelWrite(
        `/api/publish-records/${id}/work-link`, { persona, status }, 'PATCH', signal,
      )), onChanged)}>{label}</button>)}
    </details>
    <details><summary>平台后续操作</summary><button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => channelDocument(await channelRequest(`/api/publish-records/${id}/user-action?${query}`, { signal })), setUserAction)}>读取操作指引</button>
      {userAction && <><p>{channelText(userAction.message)}</p>
        {!!(userAction.state || userAction.mode) && <p className="pv-hint">{channelText(userAction.state)}{userAction.mode ? ` · ${channelText(userAction.mode)}` : ''}</p>}
        {actionExpiry > 0 && <p className="pv-hint">{actionExpired ? '应用接续已到期' : '应用接续到期时间'} {new Date(actionExpiry * 1000).toLocaleString()}</p>}
        {actionLink && <a className="btn" href={actionLink} onClick={(event) => { if (actionExpiry > 0 && actionExpiry <= Date.now() / 1000) event.preventDefault(); }}>打开平台应用继续</a>}
      </>}
    </details>
    <details><summary>移除此条本地记录</summary><label className="field-label"><input type="checkbox" checked={remove} onChange={(event) => setRemove(event.target.checked)} /> 我确认移除当前画像下的此条终态记录</label>
      <button className="btn btn-danger" disabled={busy || !remove} onClick={() => void perform(
        (signal) => channelRequest(`/api/publish-records/${id}?${query}`, { method: 'DELETE', signal }), () => onChanged(null),
      )}>移除记录</button></details>
    {error && <p role="alert">{error}</p>}
  </section>;
}
