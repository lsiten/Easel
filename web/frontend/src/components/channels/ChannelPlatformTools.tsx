import { useState } from 'react';
import { channelRequest, channelRows, channelText, channelWrite, scopedChannelPath } from '../../lib/channelsApi';
import type { ChannelDocument, ChannelScope } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';

export function ChannelPlatformTools({ scope, platform, workId }: {
  readonly scope: ChannelScope; readonly platform: string; readonly workId: string;
}) {
  const [items, setItems] = useState<readonly ChannelDocument[]>([]);
  const [playlist, setPlaylist] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [privacy, setPrivacy] = useState('PUBLIC');
  const [reviewed, setReviewed] = useState(false);
  const [message, setMessage] = useState('');
  const { busy, error, perform } = useChannelJob();
  if (!['youtube', 'pinterest', 'threads', 'google-business'].includes(platform)) return null;
  return <details style={{ marginTop: 'var(--space-4, 16px)' }}><summary>平台资源</summary>
    {platform === 'youtube' && <>
      <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => channelRows(await channelRequest(scopedChannelPath(platform, 'playlists', scope), { signal })), (rows) => { setItems(rows); setMessage('已读取播放列表'); })}>读取播放列表</button>
      <button className="btn btn-sm" disabled={busy || !workId} onClick={() => void perform(async (signal) => channelRows(await channelRequest(scopedChannelPath(platform, 'captions', scope, { work_id: workId }), { signal })), (rows) => { setItems(rows); setMessage('已读取所选作品字幕'); })}>读取作品字幕</button>
      <label className="field-label" htmlFor="youtube-playlist">播放列表 ID</label><input id="youtube-playlist" className="field" value={playlist} onChange={(event) => { setPlaylist(event.target.value); setReviewed(false); }} />
      <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 确认将所选作品加入此播放列表</label>
      <button className="btn btn-sm" disabled={busy || !reviewed || !playlist || !workId} onClick={() => void perform(
        (signal) => channelWrite(`/api/channels/youtube/playlists/${encodeURIComponent(playlist)}/videos`, { ...scope, work_id: workId, reviewed: true }, 'POST', signal),
        () => { setReviewed(false); setMessage('作品已加入播放列表'); },
      )}>加入播放列表</button>
    </>}
    {platform === 'pinterest' && <form onSubmit={(event) => { event.preventDefault(); void perform(
      (signal) => channelWrite('/api/channels/pinterest/boards', { ...scope, name, description, privacy, reviewed: true }, 'POST', signal),
      () => { setReviewed(false); setMessage('画板已创建，可在发布高级选项中刷新画板 ID'); },
    ); }}>
      <label className="field-label" htmlFor="pinterest-board-name">新画板名称</label><input id="pinterest-board-name" className="field" value={name} onChange={(event) => { setName(event.target.value); setReviewed(false); }} required />
      <label className="field-label" htmlFor="pinterest-board-description">画板说明</label><textarea id="pinterest-board-description" className="field" value={description} onChange={(event) => { setDescription(event.target.value); setReviewed(false); }} />
      <label className="field-label" htmlFor="pinterest-board-privacy">画板可见性</label><select id="pinterest-board-privacy" className="field" value={privacy} onChange={(event) => { setPrivacy(event.target.value); setReviewed(false); }}><option value="PUBLIC">公开</option><option value="SECRET">私密</option></select>
      <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 确认以当前账号创建画板</label>
      <button className="btn btn-sm" disabled={busy || !reviewed || !name.trim()}>创建画板</button>
    </form>}
    {platform === 'threads' && <form onSubmit={(event) => { event.preventDefault(); void perform(async (signal) => channelRows(await channelRequest(scopedChannelPath(platform, 'publish-options/location_id/values', scope, { keyword: name }), { signal })), setItems); }}>
      <label className="field-label" htmlFor="threads-location">地点关键词</label><input id="threads-location" className="field" value={name} onChange={(event) => setName(event.target.value)} />
      <button className="btn btn-sm" disabled={busy || !name.trim()}>查询地点 ID</button>
    </form>}
    {platform === 'google-business' && <form onSubmit={(event) => { event.preventDefault(); void perform(async (signal) => channelRows(await channelRequest(scopedChannelPath(platform, 'locations', scope, { business_account: name }), { signal })), setItems); }}>
      <label className="field-label" htmlFor="google-business-account">企业账户资源名，例如 accounts/123</label><input id="google-business-account" className="field" value={name} onChange={(event) => setName(event.target.value)} />
      <button className="btn btn-sm" disabled={busy || !name.trim()}>读取企业地点</button>
    </form>}
    <ul style={{ paddingLeft: 'var(--space-4, 16px)' }}>{items.map((item, index) => {
      const snippet = item.snippet && typeof item.snippet === 'object' ? Object.fromEntries(Object.entries(item.snippet)) : {};
      return <li key={channelText(item.id || item.name) || index} style={{ overflowWrap: 'anywhere', paddingBlock: 'var(--space-2, 8px)' }}>{channelText(item.title || snippet.title || snippet.name || item.name)} · {channelText(item.id || item.name)}</li>;
    })}</ul>
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </details>;
}
