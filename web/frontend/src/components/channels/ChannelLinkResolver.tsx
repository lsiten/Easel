import { useState } from 'react';
import { channelDocument, channelRequest, channelText, channelUrl, scopedChannelPath } from '../../lib/channelsApi';
import type { ChannelDocument, ChannelScope } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';

export function ChannelLinkResolver({ platform, scope, onResolved }: {
  readonly platform: string; readonly scope: ChannelScope; readonly onResolved: (id: string) => void;
}) {
  const [link, setLink] = useState('');
  const [result, setResult] = useState<ChannelDocument | null>(null);
  const { busy, error, perform } = useChannelJob();
  return <form onSubmit={(event) => { event.preventDefault(); void perform(async (signal) => channelDocument(await channelRequest(
    scopedChannelPath(platform, 'work-link', scope, { link }), { signal },
  )), (value) => { setResult(value); onResolved(channelText(value.platform_work_id)); }); }}>
    <label className="field-label" htmlFor="resolve-work-link">平台作品链接</label><input id="resolve-work-link" className="field" type="url" value={link} disabled={busy} onChange={(event) => { setLink(event.target.value); setResult(null); }} />
    <button className="btn btn-sm" type="submit" disabled={busy || !link.trim()}>解析作品链接</button>
    {result && <div style={{ overflowWrap: 'anywhere' }}><p>作品 ID {channelText(result.platform_work_id)}</p>
      {channelUrl(result.permalink) && <a href={channelUrl(result.permalink)} target="_blank" rel="noreferrer">打开规范作品链接</a>}
      {result.ownership_verified === false && <p className="pv-hint">作品归属尚未核验。</p>}
    </div>}
    {error && <p role="alert">{error}</p>}
  </form>;
}
