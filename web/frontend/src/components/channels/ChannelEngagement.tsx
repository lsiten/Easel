import { useState } from 'react';
import { channelDocument, channelNextCursor, channelRequest, channelRows, channelText, channelWrite, scopedChannelPath } from '../../lib/channelsApi';
import type { ChannelDocument, ChannelScope } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';

const ACTIONS: Readonly<Record<string, readonly (readonly [string, string])[]>> = {
  facebook: [['like', '点赞'], ['unlike', '取消点赞']],
  threads: [['hide_reply', '隐藏回复'], ['unhide_reply', '恢复回复']],
  twitter: [['like', '点赞'], ['unlike', '取消点赞'], ['bookmark', '收藏'], ['remove_bookmark', '取消收藏'],
    ['repost', '转发'], ['undo_repost', '撤销转发'], ['follow', '关注'], ['unfollow', '取消关注'], ['quote', '引用']],
};

export function ChannelEngagement({ scope, platform }: { readonly scope: ChannelScope; readonly platform: string }) {
  const [workId, setWorkId] = useState('');
  const [replyId, setReplyId] = useState('');
  const [text, setText] = useState('');
  const [action, setAction] = useState(ACTIONS[platform]?.[0]?.[0] || '');
  const [target, setTarget] = useState('');
  const [reviewed, setReviewed] = useState(false);
  const [rows, setRows] = useState<readonly ChannelDocument[]>([]);
  const [nextCursor, setNextCursor] = useState('');
  const [message, setMessage] = useState('');
  const [removeId, setRemoveId] = useState('');
  const [removeReviewed, setRemoveReviewed] = useState(false);
  const { busy, error, perform } = useChannelJob();
  function changed() { setReviewed(false); setMessage(''); }
  const actions = ACTIONS[platform] || [];
  return <section className="panel">
    <h2 className="panel-title">评论与互动</h2>
    <label className="field-label" htmlFor="engagement-work">平台作品 ID</label>
    <input id="engagement-work" className="field" value={workId} onChange={(event) => { setWorkId(event.target.value); setReplyId(''); setRows([]); setNextCursor(''); changed(); }} />
    <button className="btn" disabled={busy || !workId} onClick={() => void perform(
      (signal) => channelRequest(scopedChannelPath(platform, 'comments', scope, { work_id: workId }), { signal }),
      (value) => { setRows(channelRows(value)); setNextCursor(channelNextCursor(value)); setMessage('已读取评论'); })}>读取评论</button>
    {nextCursor && <button className="btn" disabled={busy || !workId} onClick={() => void perform(
      (signal) => channelRequest(scopedChannelPath(platform, 'comments', scope, { work_id: workId, cursor: nextCursor }), { signal }),
      (value) => { setRows(channelRows(value)); setNextCursor(channelNextCursor(value)); setMessage('已读取下一页评论'); },
    )}>下一页评论</button>}
    {rows.map((row, index) => {
      const snippet = row.snippet ? channelDocument(row.snippet) : {};
      const top = snippet.topLevelComment ? channelDocument(snippet.topLevelComment) : row;
      const content = top.snippet ? channelDocument(top.snippet) : top;
      const id = channelText(top.id || row.id || row.entity);
      const message = content.message && typeof content.message === 'object' ? channelText(channelDocument(content.message).text) : channelText(content.message);
      return <article key={id || index} style={{ paddingBlock: 'var(--space-3, 12px)', overflowWrap: 'anywhere' }}>
        <p>{channelText(content.textOriginal || content.textDisplay || content.text || content.content) || message}</p><span className="pv-hint">{id}</span>{' '}
        {platform !== 'threads' && <button className="btn btn-sm" disabled={busy} onClick={() => { setReplyId(id); changed(); }}>回复此评论</button>}
        <button className="btn btn-sm" disabled={busy} onClick={() => { setRemoveId(id); setRemoveReviewed(false); }}>选择评论以删除</button>
      </article>;
    })}
    {platform !== 'threads' && <>
    <label className="field-label" htmlFor="engagement-text">评论或回复内容</label>
    <textarea id="engagement-text" className="field" value={text} onChange={(event) => { setText(event.target.value); changed(); }} />
    <label className="field-label" htmlFor="engagement-reply">被回复的评论 ID，可选</label>
    <input id="engagement-reply" className="field" value={replyId} onChange={(event) => { setReplyId(event.target.value); changed(); }} />
    <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} />
      我已审核当前账号、作品、目标及内容，确认执行此操作</label>
    <button className="btn btn-primary" disabled={busy || !reviewed || !workId || !text.trim()} onClick={() => void perform(
      (signal) => channelWrite(`/api/channels/${platform}/works/${encodeURIComponent(workId)}/comments`,
        { ...scope, text, reply_to_id: replyId, reviewed: true }, 'POST', signal),
      () => { setMessage('平台已接收评论操作'); setReviewed(false); })}>发表评论或回复</button>
    </>}
    {platform === 'threads' && <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 已审核当前账号、目标与操作</label>}
    {actions.length > 0 && <details><summary>平台互动操作</summary>
      <label className="field-label" htmlFor="engagement-action">操作</label>
      <select id="engagement-action" className="field" value={action} onChange={(event) => { setAction(event.target.value); changed(); }}>
        {actions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
      <label className="field-label" htmlFor="engagement-target">目标作品、评论或账号 ID</label>
      <input id="engagement-target" className="field" value={target} onChange={(event) => { setTarget(event.target.value); changed(); }} />
      <button className="btn" disabled={busy || !reviewed || !target || !action} onClick={() => void perform(
        (signal) => channelWrite(`/api/channels/${platform}/engagement/${encodeURIComponent(target)}`,
          { ...scope, action, work_id: workId, text, reviewed: true }, 'POST', signal),
        () => { setMessage('平台已接收互动操作'); setReviewed(false); })}>执行已审核操作</button>
    </details>}
    <details><summary>删除评论</summary><label className="field-label" htmlFor="engagement-remove-id">评论 ID</label>
      <input id="engagement-remove-id" className="field" value={removeId} onChange={(event) => { setRemoveId(event.target.value); setRemoveReviewed(false); }} />
      <label className="field-label"><input type="checkbox" checked={removeReviewed} onChange={(event) => setRemoveReviewed(event.target.checked)} /> 确认删除当前账号有权管理的此条评论</label>
      <button className="btn btn-danger" disabled={busy || !removeReviewed || !removeId} onClick={() => void perform(
        (signal) => channelWrite(`/api/channels/${platform}/comments/${encodeURIComponent(removeId)}`, { ...scope, reviewed: true }, 'DELETE', signal),
        () => { setMessage('平台已接收评论删除操作'); setRemoveReviewed(false); },
      )}>确认删除评论</button></details>
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
