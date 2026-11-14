import { useCallback, useEffect, useRef, useState } from 'react';
import { channelError, channelRequest, channelWrite,
  readChannelAccounts, scopedChannelPath } from '../../lib/channelsApi';
import type { ChannelAccount, ChannelPlatform } from '../../lib/channelsApi';
import { ChannelWorkspace } from './ChannelWorkspace';
import { ChannelAuthorization } from './ChannelAuthorization';

export function ChannelAccounts({ persona, spec }: { readonly persona: string; readonly spec: ChannelPlatform }) {
  const [accounts, setAccounts] = useState<readonly ChannelAccount[]>([]);
  const [account, setAccount] = useState('');
  const [selectable, setSelectable] = useState<readonly ChannelAccount[]>([]);
  const [selectedUid, setSelectedUid] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [reload, setReload] = useState(0);
  const [disconnectReviewed, setDisconnectReviewed] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const platform = spec.platform;
  const connected = useCallback(() => setReload((value) => value + 1), []);
  useEffect(() => {
    const active = new AbortController();
    controller.current = active;
    channelRequest(scopedChannelPath(platform, 'accounts', { persona, account_uid: '' }), { signal: active.signal })
      .then((value) => {
        if (active.signal.aborted) return;
        const rows = readChannelAccounts(value);
        setAccounts(rows); setAccount((old) => rows.some((row) => row.account_uid === old) ? old : rows[0]?.account_uid || '');
        setError('');
      }).catch((failure: unknown) => { if (!active.signal.aborted) setError(channelError(failure)); });
    return () => active.abort();
  }, [persona, platform, reload]);
  const scope = { persona, account_uid: account };
  async function act(name: string, operation: (signal: AbortSignal) => Promise<void>) {
    const active = controller.current;
    if (!active || active.signal.aborted) return;
    setBusy(name); setError('');
    try { await operation(active.signal); }
    catch (failure: unknown) { if (!active.signal.aborted) setError(channelError(failure)); }
    finally { if (!active.signal.aborted) setBusy(''); }
  }
  const prefix = `EASEL_${platform.toUpperCase().replaceAll('-', '_')}_`;
  return <>
    <section className="panel" style={{ marginTop: 'var(--space-4, 16px)' }} aria-label="平台账号">
      <div className="account-card-head" style={{ flexWrap: 'wrap' }}><h2 className="panel-title">{spec.display_name} 账号</h2>
        <span className={`badge ${spec.configured ? 'badge-ok' : 'badge-warn'}`}>{spec.configured ? '开发者应用已配置' : '待配置开发者应用'}</span></div>
      {!spec.configured && <details><summary>配置连接所需环境项</summary>
        <p className="account-card-note">在项目 .env 中填写对应平台的开发者应用参数，保存后刷新页面。</p>
        <code>{prefix}CLIENT_ID<br />{prefix}CLIENT_SECRET{spec.auth_mode === 'oauth2' && <><br />{prefix}REDIRECT_URI</>}</code>
        {spec.platform === 'rednote-link' && <code><br />{prefix}ACCESS_TOKEN_URL</code>}
        {spec.auth_mode === 'miniapp_qr' && <code><br />{prefix}MINIAPP_CLIENT_ID<br />{prefix}MINIAPP_CLIENT_SECRET</code>}
        {spec.auth_mode !== 'app_handoff' && <p className="pv-hint">回调路径 /api/channels/{platform}/auth/callback</p>}
      </details>}
      {spec.account_binding !== 'handoff' && <><label className="field-label" htmlFor="channel-account">当前账号</label>
      <select id="channel-account" className="field" value={account} onChange={(event) => {
        setAccount(event.target.value); setSelectable([]); setDisconnectReviewed(false);
      }} disabled={!!busy}>
        {!accounts.length && <option value="">尚未连接账号</option>}
        {accounts.map((row) => <option key={row.account_uid} value={row.account_uid}>{row.display_name || row.account_uid}</option>)}
      </select></>}
      <div className="subnav-tabs" style={{ flexWrap: 'wrap', marginTop: 'var(--space-3, 12px)' }}>
        <ChannelAuthorization persona={persona} spec={spec} onConnected={connected} />
        <button className="btn" disabled={!!busy} onClick={() => setReload((value) => value + 1)}>刷新账号</button>
        {account && spec.auth_mode !== 'app_handoff' && <button className="btn" disabled={!!busy} onClick={() => void act('refresh', async (signal) => {
          await channelWrite(`/api/channels/${platform}/auth/refresh`, scope, 'POST', signal); setReload((value) => value + 1);
        })}>刷新授权</button>}
        {account && ['facebook', 'youtube', 'linkedin', 'google-business'].includes(platform) && <button className="btn" disabled={!!busy}
          onClick={() => void act('selectable', async (signal) => {
            const rows = readChannelAccounts(await channelRequest(scopedChannelPath(platform, 'accounts/selectable', scope), { signal }));
            setSelectable(rows); setSelectedUid(rows[0]?.account_uid || '');
          })}>选择频道或企业身份</button>}
      </div>
      {selectable.length > 0 && <div><label className="field-label" htmlFor="channel-selectable">可用身份</label>
        <select id="channel-selectable" className="field" value={selectedUid} onChange={(event) => setSelectedUid(event.target.value)}>
          {selectable.map((row) => <option key={row.account_uid} value={row.account_uid}>{row.display_name || row.account_uid}</option>)}</select>
        <button className="btn" disabled={!!busy || !selectedUid} onClick={() => void act('select', async (signal) => {
          await channelWrite(`/api/channels/${platform}/accounts/select`, { ...scope, selected_uid: selectedUid }, 'POST', signal);
          setSelectable([]); setAccount(selectedUid); setReload((value) => value + 1);
        })}>使用此身份</button></div>}
      {account && <details><summary>断开当前账号</summary>
        <label className="field-label"><input type="checkbox" checked={disconnectReviewed}
          onChange={(event) => setDisconnectReviewed(event.target.checked)} /> 我确认断开当前画像下的此平台账号</label>
        <button className="btn btn-danger" disabled={!disconnectReviewed || !!busy} onClick={() => void act('disconnect', async (signal) => {
          await channelRequest(scopedChannelPath(platform, `accounts/${encodeURIComponent(account)}`, scope), { method: 'DELETE', signal });
          setDisconnectReviewed(false); setReload((value) => value + 1);
        })}>断开账号</button></details>}
      {error && <p role="alert">{error}</p>}
      {busy && <p className="pv-hint" role="status">正在处理账号操作…</p>}
      {account && <p className="pv-hint">账号 ID {account}{accounts.find((row) => row.account_uid === account)?.expires_at
        ? ` · 授权到期 ${new Date(Number(accounts.find((row) => row.account_uid === account)?.expires_at) * 1000).toLocaleString()}` : ''}</p>}
    </section>
    <ChannelWorkspace key={JSON.stringify([persona, platform, account])} scope={scope} spec={spec} />
  </>;
}
