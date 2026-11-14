import { useEffect, useState } from 'react';
import { channelDocument, channelError, channelRequest, channelText, channelUrl, channelWrite, scopedChannelPath } from '../../lib/channelsApi';
import type { ChannelPlatform } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';
import { ChannelCookieBinding } from './ChannelCookieBinding';

type Authorization = { readonly state: string; readonly expires_at: number; readonly qr: string };

export function ChannelAuthorization({ persona, spec, onConnected }: {
  readonly persona: string; readonly spec: ChannelPlatform; readonly onConnected: () => void;
}) {
  const [session, setSession] = useState<Authorization | null>(null);
  const [statusError, setStatusError] = useState('');
  const { busy, error, perform } = useChannelJob();
  useEffect(() => {
    if (!session) return;
    const active = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (!session || active.signal.aborted) return;
      if (Date.now() / 1000 >= session.expires_at) { setSession(null); setStatusError('授权会话已到期，请重新连接'); return; }
      try {
        const value = channelDocument(await channelRequest(scopedChannelPath(spec.platform, 'auth/status', { persona, account_uid: '' }, { state: session.state }), { signal: active.signal }));
        if (active.signal.aborted) return;
        if (value.status === 'connected') { setSession(null); onConnected(); return; }
        if (value.status === 'failed' || value.status === 'expired') { setSession(null); setStatusError('平台授权已结束，请重新连接'); return; }
        setStatusError('');
      } catch (failure: unknown) { if (active.signal.aborted) return; setStatusError(channelError(failure)); }
      timer = setTimeout(() => void poll(), 2500);
    }
    void poll();
    return () => { active.abort(); clearTimeout(timer); };
  }, [persona, spec.platform, session, onConnected]);
  if (spec.account_binding === 'cookie') return <ChannelCookieBinding persona={persona} configured={spec.configured} onConnected={onConnected} />;
  if (spec.auth_mode === 'app_handoff') return <p className="pv-hint">此平台通过原生应用接续内容。</p>;
  return <>
    <button className="btn btn-primary" disabled={!spec.configured || busy || !!session || spec.auth_mode === 'unsupported'} onClick={() => {
      const popup = window.open('about:blank', '_blank');
      if (popup) popup.opener = null;
      setStatusError('');
      void perform(async (signal) => {
        try {
          const result = channelDocument(await channelWrite(`/api/channels/${spec.platform}/auth/start`, { persona }, 'POST', signal));
          if (signal.aborted) throw new DOMException('请求已取消', 'AbortError');
          const qr = channelText(result.qr_code || result.authorization_url);
          const image = /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\r\n]+$/.test(qr) ? qr : '';
          if (image) popup?.close();
          else {
            const url = channelUrl(result.authorization_url);
            if (!url) throw new Error('平台未返回有效授权地址');
            if (!popup) throw new Error('请允许弹出授权窗口，然后重试');
            popup.location.href = url;
          }
          if (typeof result.state !== 'string' || typeof result.expires_at !== 'number') throw new Error('平台未返回有效授权会话');
          return { state: result.state, expires_at: result.expires_at, qr: image };
        } catch (failure: unknown) { popup?.close(); throw failure; }
      }, setSession);
    }}>连接新账号</button>
    {busy && <p role="status">正在获取平台授权…</p>}
    {session && <div role="status">
      {session.qr && <img className="qr-img" src={session.qr} alt="平台账号授权二维码" style={{ maxWidth: '100%' }} />}
      <p className="pv-hint">{session.qr ? '使用平台应用扫描二维码完成授权' : '在授权窗口完成连接，账号列表会自动刷新'}</p>
      <button className="btn btn-sm" onClick={() => setSession(null)}>收起授权等待</button>
    </div>}
    {(error || statusError) && <p role="alert">{error || statusError}</p>}
  </>;
}
