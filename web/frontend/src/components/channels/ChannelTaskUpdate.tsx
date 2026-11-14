import { useRef, useState } from 'react';
import { channelDocument, channelJson, channelRequest, channelText, channelWrite } from '../../lib/channelsApi';
import type { ChannelDocument } from '../../lib/channelsApi';
import { OwnedMediaPicker } from './OwnedMediaPicker';
import { useChannelJob } from './useChannelJob';

export function ChannelTaskUpdate({ persona, task }: { readonly persona: string; readonly task: ChannelDocument }) {
  const original = task.intent ? channelDocument(task.intent) : {};
  const [title, setTitle] = useState(channelText(original.title));
  const [body, setBody] = useState(channelText(original.body));
  const [options, setOptions] = useState('{}');
  const [cover, setCover] = useState('');
  const [approval, setApproval] = useState<ChannelDocument | null>(null);
  const [result, setResult] = useState<ChannelDocument | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const requestKey = useRef('');
  const { busy, error, perform } = useChannelJob();
  const scope = { persona, account_uid: channelText(task.account_uid) };
  const base = `/api/publish-flows/tasks/${encodeURIComponent(channelText(task.id))}/update`;
  function intent() { return { ...scope, title, body, options: channelJson(options), cover_path: cover }; }
  function changed() { setApproval(null); setReviewed(false); }
  const current = approval?.current ? channelDocument(approval.current) : null;
  const snippet = current?.snippet ? channelDocument(current.snippet) : current;
  return <details style={{ marginTop: 'var(--space-4, 16px)' }}><summary>更新已发布作品</summary>
    <label className="field-label" htmlFor="task-update-title">更新后的标题</label><input id="task-update-title" className="field" maxLength={100} value={title} disabled={busy} onChange={(event) => { setTitle(event.target.value); changed(); }} />
    <label className="field-label" htmlFor="task-update-body">更新后的正文或说明</label><textarea id="task-update-body" className="field" maxLength={task.platform === 'pinterest' ? 800 : 5000} value={body} disabled={busy} onChange={(event) => { setBody(event.target.value); changed(); }} />
    {task.platform === 'youtube' && <><label className="field-label">更新视频封面，可选</label><OwnedMediaPicker persona={persona} kind="image" value={cover} disabled={busy} onChange={(value) => { setCover(value); changed(); }} /></>}
    <details><summary>作品更新选项</summary><label className="field-label" htmlFor="task-update-options">更新选项 JSON</label>
      <textarea id="task-update-options" className="field" value={options} disabled={busy} onChange={(event) => { setOptions(event.target.value); changed(); }} /></details>
    <button className="btn" disabled={busy || (task.platform === 'youtube' && !title.trim())} onClick={() => void perform(async (signal) => channelDocument(await channelWrite(`${base}/precheck`, intent(), 'POST', signal)), (value) => {
      setApproval(value); setReviewed(false); requestKey.current = crypto.randomUUID();
    })}>读取平台当前版本并预检更新</button>
    {current && <section aria-label="平台当前作品版本"><h4>平台当前内容</h4><p>{channelText(snippet?.title)}</p>
      <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{channelText(snippet?.description)}</p>
      <button className="btn btn-sm" disabled={busy} onClick={() => { setTitle(channelText(snippet?.title)); setBody(channelText(snippet?.description)); changed(); }}>采用平台当前内容继续编辑</button>
      <details><summary>当前选项与版本信息</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(current, null, 2)}</pre></details>
      <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 已核对平台当前内容与上述修改，确认更新此任务的原作品</label>
      <button className="btn btn-primary" disabled={busy || !reviewed} onClick={() => void perform(async (signal) => channelDocument(await channelWrite(base, {
        ...intent(), reviewed: true, request_key: requestKey.current, viewed_revision: approval?.viewed_revision,
        approval_digest: approval?.approval_digest, approval_expires_at: approval?.approval_expires_at,
      }, 'POST', signal)), (value) => { setResult(value); setReviewed(false); })}>确认更新已发布作品</button>
    </section>}
    {result && <div role="status"><p>更新状态 {channelText(result.state)} · {channelText(result.stage)}{result.cover_status ? ` · 封面 ${channelText(result.cover_status)}` : ''}</p>
      {!!result.error && <p role="alert">{channelText(result.error)}</p>}
      <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => channelDocument(await channelRequest(`${base}/${encodeURIComponent(channelText(result.update_id))}?${new URLSearchParams(scope)}`, { signal })), setResult)}>刷新更新结果</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </details>;
}
