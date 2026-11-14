import { useState } from 'react';
import { channelDocument, channelJson, channelRequest, channelText, channelUrl, channelWorkDetails, channelWrite, scopedChannelPath } from '../../lib/channelsApi';
import type { ChannelDocument, ChannelScope } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';

export function ChannelWorkActions({ scope, platform, workId, updateSupported }: {
  readonly scope: ChannelScope; readonly platform: string; readonly workId: string; readonly updateSupported: boolean;
}) {
  const [work, setWork] = useState<ChannelDocument | null>(null);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [options, setOptions] = useState('{}');
  const [reviewed, setReviewed] = useState(false);
  const [deleteReviewed, setDeleteReviewed] = useState(false);
  const [notice, setNotice] = useState('');
  const { busy, error, perform } = useChannelJob();
  const remove = ['youtube', 'twitter', 'pinterest', 'instagram', 'facebook', 'threads'].includes(platform);
  const endpoint = `/api/channels/${encodeURIComponent(platform)}/works/${encodeURIComponent(workId)}`;
  return <details><summary>作品详情与管理</summary>
    <button className="btn btn-sm" disabled={busy || !workId} onClick={() => void perform(async (signal) => channelWorkDetails(await channelRequest(scopedChannelPath(platform, `works/${encodeURIComponent(workId)}`, scope), { signal })), (value) => {
      setWork(value); setNotice('已读取平台作品'); setReviewed(false);
      const snippet = value.snippet ? channelDocument(value.snippet) : {};
      setTitle(channelText(snippet.title || value.title)); setBody(channelText(snippet.description || value.description || value.caption || value.text));
    })}>读取所选作品详情</button>
    {work && <><h4>{title || workId}</h4><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{body}</p>
      {channelUrl(work.permalink || work.url) && <a href={channelUrl(work.permalink || work.url)} target="_blank" rel="noreferrer">打开平台作品</a>}
    </>}
    {updateSupported && work && <form onSubmit={(event) => { event.preventDefault(); void perform(
      (signal) => channelWrite(endpoint, { ...scope, reviewed: true, content: { title, body, options: channelJson(options) } }, 'PATCH', signal),
      () => { setNotice('平台已更新作品'); setReviewed(false); },
    ); }}>
      <label className="field-label" htmlFor="work-edit-title">作品标题</label><input id="work-edit-title" className="field" value={title} disabled={busy} onChange={(event) => { setTitle(event.target.value); setReviewed(false); }} />
      <label className="field-label" htmlFor="work-edit-body">作品说明</label><textarea id="work-edit-body" className="field" value={body} disabled={busy} onChange={(event) => { setBody(event.target.value); setReviewed(false); }} />
      <details><summary>平台更新选项</summary><label className="field-label" htmlFor="work-edit-options">选项 JSON</label><textarea id="work-edit-options" className="field" value={options} disabled={busy} onChange={(event) => { setOptions(event.target.value); setReviewed(false); }} /></details>
      <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 已审核当前账号和作品修改内容</label>
      <button className="btn" disabled={busy || !reviewed}>确认更新平台作品</button>
    </form>}
    {remove && <details><summary>删除平台作品</summary><label className="field-label"><input type="checkbox" checked={deleteReviewed} onChange={(event) => setDeleteReviewed(event.target.checked)} /> 确认删除当前账号的作品 {workId}</label>
      <button className="btn btn-danger" disabled={busy || !deleteReviewed || !workId} onClick={() => void perform(
        (signal) => channelWrite(endpoint, { ...scope, reviewed: true }, 'DELETE', signal),
        () => { setNotice('平台已接收作品删除操作'); setDeleteReviewed(false); setWork(null); },
      )}>确认删除平台作品</button></details>}
    {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
  </details>;
}
