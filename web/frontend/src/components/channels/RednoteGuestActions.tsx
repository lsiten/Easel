import { useEffect, useRef, useState } from 'react';
import { channelError } from '../../lib/channelsApi';
import type { ChannelDocument } from '../../lib/channelsApi';
import { getRednoteSdk, loadRednoteSdk, rednoteShareRequest } from '../../lib/nativeHandoffApi';
import type { PublicHandoff } from '../../lib/nativeHandoffApi';
import { useChannelJob } from './useChannelJob';

export function RednoteGuestActions({ content, result, token }: {
  readonly content: PublicHandoff; readonly result: ChannelDocument; readonly token: string;
}) {
  const [ready, setReady] = useState(Boolean(getRednoteSdk()));
  const [reviewed, setReviewed] = useState(false);
  const [message, setMessage] = useState('');
  const [sdkError, setSdkError] = useState('');
  const alive = useRef(true);
  const { busy, error, perform } = useChannelJob();
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  return <section aria-label="小红书应用接续">
    <p className="pv-hint">标题和正文通过上方按钮复制，在小红书应用中粘贴。分享 SDK 接续图片或视频。</p>
    <p><a href="https://agora.xiaohongshu.com/doc/js" target="_blank" rel="noreferrer">小红书官方 SDK 接入说明</a></p>
    <button className="btn" disabled={busy || ready} onClick={() => void perform(async () => loadRednoteSdk(), () => { setReady(true); setMessage('官方分享 SDK 已加载'); })}>加载官方分享 SDK</button>
    {ready && <>
      <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 已核对图片或视频，确认在小红书中继续</label>
      <button className="btn btn-primary" disabled={!reviewed || busy} onClick={() => {
        setSdkError('');
        try {
          const sdk = getRednoteSdk();
          if (!sdk) throw new Error('请先加载官方分享 SDK');
          sdk.share({ ...rednoteShareRequest(result.verifyConfig, content.media, content.template_id, token), fail: (failure: unknown) => {
            if (alive.current) { setSdkError(channelError(failure)); setMessage(''); }
          } });
          setReviewed(false); setMessage('已请求在小红书中继续，请在应用内确认发布结果');
        } catch (failure: unknown) { setSdkError(channelError(failure)); }
      }}>打开小红书继续分享</button>
    </>}
    <details><summary>分享 SDK 配置信息</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ verifyConfig: result.verifyConfig, sdk_method: result.sdk_method }, null, 2)}</pre></details>
    {message && <p role="status">{message}</p>}{(error || sdkError) && <p role="alert">{error || sdkError}</p>}
  </section>;
}
