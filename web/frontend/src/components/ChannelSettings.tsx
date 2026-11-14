import { useCallback, useEffect, useState } from 'react';
import { createCallerKey, createShortLink, deleteShortLink, loadCallerKeys, loadShortLinks,
  revokeCallerKey, SettingsInputError, shortLinkUrl } from '../lib/channelSettingsApi';
import type { CallerKey, ShortLink } from '../lib/channelSettingsApi';
import { ChannelProfileSettings } from './ChannelProfileSettings';
import { ChannelAccountGroups } from './ChannelAccountGroups';
import { useChannelJob } from './channels/useChannelJob';

const ACTIONS = { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 'var(--space-3, 12px)' } as const;
const LIST = { listStyle: 'none', padding: 0, margin: 0 } as const;
const ROW = { ...ACTIONS, justifyContent: 'space-between', paddingBlock: 'var(--space-3, 12px)', borderBottom: '1px solid var(--border)' } as const;

export function ChannelSettings({ persona }: { readonly persona: string }) {
  return persona ? <PersonaSettings key={persona} persona={persona} /> : <p className="panel">选择画像后管理设置。</p>;
}

function PersonaSettings({ persona }: { readonly persona: string }) {
  const [keys, setKeys] = useState<readonly CallerKey[]>([]);
  const [links, setLinks] = useState<readonly ShortLink[]>([]);
  const [ready, setReady] = useState(false);
  const [keyName, setKeyName] = useState('');
  const [permission, setPermission] = useState<'read' | 'write'>('read');
  const [secret, setSecret] = useState<{ readonly id: string; readonly value: string } | null>(null);
  const [url, setUrl] = useState('');
  const [expires, setExpires] = useState('');
  const [message, setMessage] = useState('');
  const [section, setSection] = useState<'keys' | 'links'>('keys');
  const { busy, error, perform } = useChannelJob();
  const load = useCallback(() => {
    setSection('keys'); setMessage('');
    void perform((signal) => Promise.all([loadCallerKeys(persona, signal), loadShortLinks(persona, signal)]), ([newKeys, newLinks]) => {
      setKeys(newKeys); setLinks(newLinks); setReady(true);
    });
  }, [persona, perform]);
  useEffect(load, [load]);
  function copy(value: string, label: string, owner: 'keys' | 'links') {
    setSection(owner);
    void perform(async () => { await navigator.clipboard.writeText(value); return label; }, (name) => setMessage(`${name}已复制`));
  }
  const feedback = <>{error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {busy && ready && <p className="pv-hint" role="status">正在处理访问设置…</p>}</>;
  return <>
    <ChannelProfileSettings persona={persona} />
    <ChannelAccountGroups persona={persona} />
    <section className="panel" aria-label="调用密钥">
      <div className="account-card-head"><h2 className="panel-title">调用密钥</h2>
        <button className="btn" disabled={busy} onClick={load}>刷新列表</button></div>
      {!ready && !error && <p role="status">正在加载访问设置…</p>}
      <form onSubmit={(event) => { event.preventDefault(); setMessage(''); setSecret(null); setSection('keys');
        void perform((signal) => createCallerKey(persona, keyName, permission, signal), (value) => {
          setKeys((current) => [value.entry, ...current]); setSecret({ id: value.entry.id, value: value.secret }); setKeyName(''); setMessage('调用密钥已创建');
        }); }}>
        <fieldset disabled={busy || !ready} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
          <label className="field-label">密钥名称<input className="field" required maxLength={200} value={keyName}
            onChange={(event) => setKeyName(event.target.value)} /></label>
          <label className="field-label">权限<select className="field" value={permission}
            onChange={(event) => setPermission(event.target.value === 'write' ? 'write' : 'read')}>
            <option value="read">仅读取当前画像</option><option value="write">读取并操作当前画像</option></select></label>
          <button className="btn" type="submit" disabled={!keyName.trim()} style={{ marginTop: 'var(--space-3, 12px)' }}>创建调用密钥</button>
        </fieldset>
      </form>
      {secret && <div style={{ marginBlock: 'var(--space-4, 16px)' }}>
        <label className="field-label">新建 API 密钥<input className="field" readOnly autoComplete="off" spellCheck={false} value={secret.value} /></label>
        <p className="pv-hint">密钥仅在创建时展示，请复制保存。</p>
        <div style={ACTIONS}><button className="btn" disabled={busy} onClick={() => copy(secret.value, '密钥', 'keys')}>复制密钥</button>
          <button className="btn" onClick={() => { setSecret(null); setMessage('密钥已隐藏'); }}>我已保存</button></div>
      </div>}
      <ul style={LIST}>
        {keys.map((key) => <li key={key.id} style={ROW}>
          <div style={{ minWidth: 0, overflowWrap: 'anywhere' }}><strong>{key.name}</strong>
            <p className="pv-hint">{key.permission === 'write' ? '可读写' : '仅读'} · 创建于 {new Date(key.created_at * 1000).toLocaleString()}
              {key.last_used_at !== null ? ` · 最近使用 ${new Date(key.last_used_at * 1000).toLocaleString()}` : ' · 尚未使用'}</p></div>
          <button className="btn btn-danger" disabled={busy} aria-label={`撤销密钥 ${key.name}`} onClick={() => {
            setMessage(''); setSection('keys'); void perform((signal) => revokeCallerKey(persona, key.id, signal), () => {
              setKeys((current) => current.filter((item) => item.id !== key.id)); setMessage('调用密钥已撤销');
              setSecret((current) => current?.id === key.id ? null : current);
            });
          }}>撤销</button>
        </li>)}
      </ul>
      {ready && !keys.length && <p className="pv-hint">当前画像尚未创建调用密钥。</p>}
      {section === 'keys' && feedback}
    </section>
    <section className="panel" aria-label="短链接">
      <h2 className="panel-title">短链接</h2>
      <form onSubmit={(event) => { event.preventDefault(); setMessage(''); setSection('links');
        void perform(async (signal) => {
          const expiry = expires ? new Date(expires).getTime() / 1000 : null;
          if (expiry !== null && (!Number.isFinite(expiry) || expiry <= Date.now() / 1000)) throw new SettingsInputError('expires', '请选择未来的到期时间');
          await createShortLink(persona, url, expiry, signal);
          return loadShortLinks(persona, signal);
        }, (value) => { setLinks(value); setUrl(''); setExpires(''); setMessage('短链接已创建'); }); }}>
        <fieldset disabled={busy || !ready} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
          <label className="field-label">目标地址<input className="field" type="url" required maxLength={8192} value={url}
            onChange={(event) => setUrl(event.target.value)} /></label>
          <label className="field-label">到期时间（可选）<input className="field" type="datetime-local" value={expires}
            onChange={(event) => setExpires(event.target.value)} /></label>
          <button className="btn" type="submit" disabled={!url.trim()} style={{ marginTop: 'var(--space-3, 12px)' }}>创建短链接</button>
        </fieldset>
      </form>
      <ul style={LIST}>
        {links.map((link) => <li key={link.code} style={ROW}>
          <div style={{ minWidth: 0, flex: '1 1 16rem', overflowWrap: 'anywhere' }}>
            <a href={shortLinkUrl(link.code)} target="_blank" rel="noreferrer">{shortLinkUrl(link.code)}</a>
            <p className="pv-hint">目标地址 {link.url}</p><p className="pv-hint">{link.expires_at === null ? '长期有效'
              : `${link.expires_at <= Date.now() / 1000 ? '已到期' : '到期时间'} ${new Date(link.expires_at * 1000).toLocaleString()}`}</p>
          </div>
          <div style={ACTIONS}><button className="btn" disabled={busy} aria-label={`复制短链接 ${link.code}`} onClick={() => copy(shortLinkUrl(link.code), '短链接', 'links')}>复制</button>
            <button className="btn btn-danger" disabled={busy} aria-label={`删除短链接 ${link.code}`} onClick={() => {
              setMessage(''); setSection('links'); void perform((signal) => deleteShortLink(persona, link.code, signal), () => {
                setLinks((current) => current.filter((item) => item.code !== link.code)); setMessage('短链接已删除');
              });
            }}>删除</button></div>
        </li>)}
      </ul>
      {ready && !links.length && <p className="pv-hint">当前画像尚未创建短链接。</p>}
      {section === 'links' && feedback}
    </section>
  </>;
}
