import { useEffect, useState } from 'react';
import { channelDocument, channelJson, channelRequest, channelRows, channelText, scopedChannelPath } from '../../lib/channelsApi';
import type { ChannelDocument, ChannelScope } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';

export function ChannelPublishOptions({ scope, platform, value, disabled, onChange }: {
  readonly scope: ChannelScope; readonly platform: string; readonly value: string; readonly disabled: boolean; readonly onChange: (value: string) => void;
}) {
  const [values, setValues] = useState<readonly ChannelDocument[]>([]);
  const [creator, setCreator] = useState<ChannelDocument | null>(null);
  const [reload, setReload] = useState(0);
  const { busy, error, perform } = useChannelJob();
  useEffect(() => {
    if (!['pinterest', 'tiktok'].includes(platform)) return;
    void perform(async (signal) => channelDocument(await channelRequest(scopedChannelPath(platform, 'publish-options', scope), { signal })), (document) => {
      setValues(channelRows(document));
      if (platform === 'tiktok') setCreator(document.data ? channelDocument(document.data) : document);
    });
  }, [platform, scope, reload, perform]);
  if (!['pinterest', 'tiktok'].includes(platform)) return null;
  let options: ChannelDocument | null;
  try { options = channelJson(value); }
  catch (failure: unknown) { if (failure instanceof Error) options = null; else throw failure; }
  function update(field: string, selected: string) {
    if (options) onChange(JSON.stringify({ ...options, [field]: selected }, null, 2));
  }
  const privacy = Array.isArray(creator?.privacy_level_options) ? creator.privacy_level_options.filter((item): item is string => typeof item === 'string') : [];
  return <>
    {platform === 'pinterest' && <><label className="field-label" htmlFor="publish-board">画板</label><select id="publish-board" className="field" value={channelText(options?.boardId)} disabled={disabled || busy || !options} onChange={(event) => update('boardId', event.target.value)}>
      <option value="">选择已连接账号的画板</option>{values.map((row) => <option key={channelText(row.id)} value={channelText(row.id)}>{channelText(row.name) || channelText(row.id)}</option>)}
    </select></>}
    {platform === 'tiktok' && <><label className="field-label" htmlFor="publish-privacy">可见范围</label><select id="publish-privacy" className="field" value={channelText(options?.privacy_level)} disabled={disabled || busy || !options} onChange={(event) => update('privacy_level', event.target.value)}>
      <option value="">选择平台允许的可见范围</option>{privacy.map((level) => <option key={level} value={level}>{level}</option>)}
    </select>{creator && <p className="pv-hint">平台允许的视频时长上限 {channelText(creator.max_video_post_duration_sec) || '以平台校验为准'} 秒</p>}</>}
    <button className="btn btn-sm" disabled={disabled || busy} onClick={() => setReload((previous) => previous + 1)}>刷新平台发布条件</button>
    {error && <p role="alert">{error}</p>}
  </>;
}
