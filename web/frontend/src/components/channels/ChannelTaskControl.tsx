import { useState } from 'react';
import { CHANNEL_NAMES, channelDocument, channelRequest, channelText, channelWrite, readChannelApproval } from '../../lib/channelsApi';
import type { ChannelDocument } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';
import { ChannelTaskUpdate } from './ChannelTaskUpdate';
import { ChannelReapproval } from './ChannelReapproval';

export function ChannelTaskControl({ persona, task, onChanged }: {
  readonly persona: string; readonly task: ChannelDocument; readonly onChanged: (task: ChannelDocument) => void;
}) {
  const [action, setAction] = useState(task.state === 'failed' ? 'retry' : 'publish-now');
  const [when, setWhen] = useState('');
  const [approval, setApproval] = useState<Readonly<{ approval_digest: string; approval_expires_at?: number }> | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [cancelReview, setCancelReview] = useState(false);
  const { busy, error, perform } = useChannelJob();
  const intent = task.intent ? channelDocument(task.intent) : null;
  const platform = channelText(task.platform);
  const id = channelText(task.id);
  const state = channelText(task.state);
  const official = Boolean(CHANNEL_NAMES[platform]);
  function plannedIntent() {
    if (!intent) throw new Error('此任务缺少可重新审核的内容');
    if (!official) return { persona, title: channelText(intent.title), body: channelText(intent.body),
      media: intent.media || [], tags: channelText(intent.tags), context: intent.context || {}, reviewed: true };
    return { persona, account_uid: channelText(task.account_uid), title: channelText(intent.title), body: channelText(intent.body),
      media: intent.media || [], options: intent.options || {}, context: intent.context || {}, cover_path: channelText(intent.cover_path),
      scheduled_for: action === 'reschedule' ? new Date(when).toISOString() : '' };
  }
  return <section style={{ marginTop: 'var(--space-4, 16px)' }} aria-label="发布计划详情">
    <h3 className="panel-title">计划详情</h3><p className="pv-hint" style={{ overflowWrap: 'anywhere' }}>{id} · {state}</p>
    <p className="pv-hint" style={{ overflowWrap: 'anywhere' }}>画像 {persona} · 平台账号 {channelText(task.account_uid)}</p>
    {!!task.publish_at && <p className="pv-hint">计划发布时间 {new Date(Number(task.publish_at) * 1000).toLocaleString()}</p>}
    {intent && <><h4>{channelText(intent.title)}</h4><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{channelText(intent.body)}</p>
      <details><summary>媒体与平台选项</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ media: intent.media, options: intent.options }, null, 2)}</pre></details></>}
    {['queued', 'failed'].includes(state) && <>
      {official && !!task.run_id && <>
        <p className="pv-hint">重新审核冻结内容后，任务会保留原定时间等待执行。</p>
        <ChannelReapproval platform={platform} runId={channelText(task.run_id)} scope={{ persona, account_uid: channelText(task.account_uid) }}
          onChanged={() => void perform(async (signal) => channelDocument(await channelRequest(`/api/publish-flows/tasks/${encodeURIComponent(id)}/detail?${new URLSearchParams({ persona })}`, { signal })), onChanged)} />
      </>}
      <label className="field-label" htmlFor="task-action">计划操作</label><select id="task-action" className="field" value={action} disabled={busy} onChange={(event) => { setAction(event.target.value); setApproval(null); setReviewed(false); }}>
        {state === 'failed' ? <option value="retry">重新发布</option> : <><option value="publish-now">立即发布</option><option value="reschedule">调整时间</option></>}
      </select>
      {action === 'reschedule' && <><label className="field-label" htmlFor="task-time">新的发布时间</label><input id="task-time" className="field" type="datetime-local" value={when} disabled={busy} onChange={(event) => { setWhen(event.target.value); setApproval(null); setReviewed(false); }} /></>}
      <button className="btn" disabled={busy || !intent || (action === 'reschedule' && !when)} onClick={() => void perform(async (signal) => {
        const value = await channelWrite(`${official ? '/api/channel-publish' : '/api/publish'}/${encodeURIComponent(platform)}/precheck`, plannedIntent(), 'POST', signal);
        if (official) return readChannelApproval(value);
        const digest = channelText(channelDocument(value).approvalDigest);
        if (!digest) throw new Error('平台未返回有效审核凭据');
        return { approval_digest: digest };
      }, (value) => { setApproval(value); setReviewed(false); })}>重新预检计划内容</button>
      {approval && <><label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 已审核任务账号、内容与新的发布时间</label>
        <button className="btn btn-primary" disabled={busy || !reviewed} onClick={() => void perform(async (signal) => channelDocument(await channelWrite(
          `/api/publish-flows/tasks/${encodeURIComponent(id)}/${action}`, { persona, reviewed: true, ...approval,
            ...(action === 'reschedule' ? { publish_at: new Date(when).toISOString() } : {}) }, 'POST', signal,
        )), (value) => { setApproval(null); setReviewed(false); onChanged(value); })}>确认执行计划操作</button></>}
      <details><summary>取消此计划</summary><label className="field-label"><input type="checkbox" checked={cancelReview} onChange={(event) => setCancelReview(event.target.checked)} /> 确认取消尚未执行的任务</label>
        <button className="btn btn-danger" disabled={busy || !cancelReview} onClick={() => void perform(async (signal) => channelDocument(await channelWrite(
          `/api/publish-flows/tasks/${encodeURIComponent(id)}/cancel`, { persona }, 'POST', signal,
        )), onChanged)}>取消计划</button></details>
    </>}
    {state === 'published' && ['youtube', 'pinterest'].includes(platform) && <ChannelTaskUpdate persona={persona} task={task} />}
    {error && <p role="alert">{error}</p>}
  </section>;
}
