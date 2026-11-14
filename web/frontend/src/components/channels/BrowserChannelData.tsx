import { useState } from 'react';
import { fetchAccountAnalytics } from '../../lib/api';
import type { AccountAnalytics } from '../../lib/api';
import { channelUrl } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';

export function BrowserChannelData({ persona, platform }: { readonly persona: string; readonly platform: string }) {
  const [data, setData] = useState<AccountAnalytics | null>(null);
  const { busy, error, perform } = useChannelJob();
  return <section className="panel">
    <h2 className="panel-title">浏览器账号数据</h2>
    <button className="btn btn-primary" disabled={busy} onClick={() => void perform(
      () => fetchAccountAnalytics(platform, persona), setData,
    )}>{busy ? '正在读取账号数据…' : '读取账号与作品数据'}</button>
    {data && <>
      <p>{data.nickname}</p>
      <dl>{[['粉丝', data.followers], ['获赞', data.likes], ['关注', data.following], ['作品', data.posts]].map(([label, value]) =>
        <div key={label} style={{ display: 'flex', gap: 'var(--space-3, 12px)' }}><dt>{label}</dt><dd>{value ?? '—'}</dd></div>)}
      </dl>
      {data.warning && <p role="status">{data.warning}</p>}
      {data.notes.map((note) => <article key={note.url || note.title} style={{ marginBlock: 'var(--space-3, 12px)', overflowWrap: 'anywhere' }}>
        {channelUrl(note.url) ? <a href={channelUrl(note.url)} target="_blank" rel="noreferrer">{note.title || '查看平台作品'}</a> : <span>{note.title}</span>}
        {note.stat && <p className="pv-hint">{note.stat}</p>}
      </article>)}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
