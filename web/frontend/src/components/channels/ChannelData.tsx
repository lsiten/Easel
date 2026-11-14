import { useState } from 'react';
import { channelDocument, channelNextCursor, channelRequest, channelRows, channelText, channelUrl, channelWorkId, scopedChannelPath } from '../../lib/channelsApi';
import type { ChannelDocument, ChannelPlatform, ChannelScope } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';
import { ChannelWorkActions } from './ChannelWorkActions';
import { ChannelPlatformTools } from './ChannelPlatformTools';
import { ChannelLinkResolver } from './ChannelLinkResolver';

function numberFields(document: ChannelDocument): readonly (readonly [string, unknown])[] {
  const nested = document.account_metrics || document.content_metrics;
  const metrics = nested !== null && typeof nested === 'object' && !Array.isArray(nested) ? channelDocument(nested) : document;
  return Object.entries(metrics).filter(([, value]) => typeof value === 'number' || value === null);
}

export function ChannelData({ scope, spec }: { readonly scope: ChannelScope; readonly spec: ChannelPlatform }) {
  const [rows, setRows] = useState<readonly ChannelDocument[]>([]);
  const [profile, setProfile] = useState<ChannelDocument | null>(null);
  const [analytics, setAnalytics] = useState<ChannelDocument | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [cursor, setCursor] = useState('');
  const [nextCursor, setNextCursor] = useState('');
  const [workId, setWorkId] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const { busy, error, perform } = useChannelJob();
  return <section className="panel">
    <h2 className="panel-title">作品与数据</h2>
    <div className="subnav-tabs" style={{ flexWrap: 'wrap' }}>
      {spec.account_binding !== 'handoff' && <button className="btn" disabled={busy} onClick={() => void perform(
        (signal) => channelRequest(scopedChannelPath(spec.platform, 'profile', scope), { signal }),
        (value) => setProfile(channelDocument(value)))}>读取账号资料</button>}
      {spec.work_list_supported && <button className="btn" disabled={busy} onClick={() => void perform(
        (signal) => channelRequest(scopedChannelPath(spec.platform, 'works', scope, { cursor }), { signal }),
        (value) => { setRows(channelRows(value)); setNextCursor(channelNextCursor(value)); setLoaded(true); })}>读取作品列表</button>}
      {spec.work_list_supported && nextCursor && <button className="btn" disabled={busy} onClick={() => void perform(
        (signal) => channelRequest(scopedChannelPath(spec.platform, 'works', scope, { cursor: nextCursor }), { signal }),
        (value) => { setCursor(nextCursor); setRows(channelRows(value)); setNextCursor(channelNextCursor(value)); setLoaded(true); },
      )}>下一页作品</button>}
    </div>
    {profile && <p>{channelText(profile.display_name || profile.name)} <span className="pv-hint">{channelText(profile.account_uid || profile.id)}</span></p>}
    {spec.works && <ChannelLinkResolver platform={spec.platform} scope={scope} onResolved={setWorkId} />}
    {spec.work_list_supported && <details><summary>列表翻页</summary><label className="field-label" htmlFor="channel-cursor">平台返回的游标</label>
      <input id="channel-cursor" className="field" value={cursor} onChange={(event) => setCursor(event.target.value)} /></details>}
    {loaded && !rows.length && <p className="pv-hint">平台未返回可显示的作品。</p>}
    {rows.map((row, index) => {
      const snippet = row.snippet !== null && typeof row.snippet === 'object' && !Array.isArray(row.snippet) ? channelDocument(row.snippet) : {};
      const id = channelWorkId(row);
      const url = channelUrl(row.permalink || row.permalink_url || row.share_url || row.url);
      return <article key={id || index} style={{ borderBottom: '1px solid var(--border)', paddingBlock: 'var(--space-3, 12px)', overflowWrap: 'anywhere' }}>
        <strong>{channelText(snippet.title || row.title || row.caption || row.text || row.message) || id || '平台作品'}</strong>
        <p className="pv-hint">{id}</p>
        {url && <a href={url} target="_blank" rel="noreferrer">查看作品</a>}{' '}
        {id && <button className="btn btn-sm" onClick={() => setWorkId(id)}>选择此作品查询数据</button>}
      </article>;
    })}
    {spec.work_detail_supported && <>
      <label className="field-label" htmlFor="channel-metric-work">作品 ID，留空读取账号数据</label>
      <input id="channel-metric-work" className="field" value={workId} onChange={(event) => setWorkId(event.target.value)} />
      {workId && <ChannelWorkActions key={JSON.stringify([scope.persona, scope.account_uid, spec.platform, workId])} scope={scope} platform={spec.platform} workId={workId} updateSupported={spec.update_supported} />}
    </>}
    {spec.analytics && <>
      <label className="field-label" htmlFor="channel-range-start">开始日期</label><input id="channel-range-start" type="date" className="field" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
      <label className="field-label" htmlFor="channel-range-end">结束日期</label><input id="channel-range-end" type="date" className="field" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
      <button className="btn" disabled={busy} onClick={() => void perform(
        (signal) => channelRequest(scopedChannelPath(spec.platform, 'analytics', scope, {
          work_id: workId, start_date: startDate, end_date: endDate,
        }), { signal }), (value) => setAnalytics(channelDocument(value)))}>读取平台指标</button>
      {analytics && <dl>{numberFields(analytics).map(([key, value]) => <div key={key} style={{ display: 'flex', gap: 'var(--space-3, 12px)' }}>
        <dt>{key}</dt><dd>{value === null ? '—' : channelText(value)}</dd></div>)}</dl>}
    </>}
    {!spec.works && !spec.analytics && <p className="pv-hint">此平台当前提供授权及身份管理。</p>}
    <ChannelPlatformTools scope={scope} platform={spec.platform} workId={workId} />
    {busy && <p role="status">正在读取平台…</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
