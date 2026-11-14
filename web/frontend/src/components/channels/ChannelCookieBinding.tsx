import { useState } from 'react';
import { channelWrite } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';

export function ChannelCookieBinding({ persona, configured, onConnected }: {
  readonly persona: string; readonly configured: boolean; readonly onConnected: () => void;
}) {
  const [cookie, setCookie] = useState('');
  const { busy, error, perform } = useChannelJob();
  return <form onSubmit={(event) => { event.preventDefault(); void perform(
    (signal) => channelWrite('/api/channels/wechat-channels-open/accounts/bind-cookie', { persona, cookie }, 'POST', signal),
    () => { setCookie(''); onConnected(); },
  ); }}>
    <label className="field-label" htmlFor="channels-cookie">视频号后台登录 Cookie</label>
    <input id="channels-cookie" className="field" type="password" autoComplete="off" value={cookie} disabled={busy} onChange={(event) => setCookie(event.target.value)} />
    <button className="btn" type="submit" disabled={busy || !configured || !cookie.trim()}>验证并绑定视频号账号</button>
    {error && <p role="alert">{error}</p>}
  </form>;
}
