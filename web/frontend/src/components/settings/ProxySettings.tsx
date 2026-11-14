import { useEffect, useState } from 'react';
import { fetchProxySettings, saveProxySettings } from '../../lib/api';
import type { ProxyMode, ProxySettingsView } from '../../lib/api';
import { IconGlobe } from '../settingsIcons';

const FIELDS = [
  ['HTTP_PROXY', 'httpProxy', 'http://127.0.0.1:7890'],
  ['HTTPS_PROXY', 'httpsProxy', 'http://127.0.0.1:7890'],
  ['ALL_PROXY', 'allProxy', 'socks5h://127.0.0.1:7890（可选）'],
  ['NO_PROXY', 'noProxy', 'localhost,127.0.0.1,::1'],
] as const;

type FieldKey = typeof FIELDS[number][0];
type ValueKey = typeof FIELDS[number][1];

const MODES: readonly { value: ProxyMode; label: string; note: string }[] = [
  { value: 'system', label: '跟随系统', note: '继承当前终端或系统环境的代理变量' },
  { value: 'custom', label: '自定义', note: '由 Easel 保存并注入自己的子进程' },
  { value: 'direct', label: '强制直连', note: '清除代理变量，适合当前网络可直连时' },
] as const;

export function ProxySettings() {
  const [config, setConfig] = useState<ProxySettingsView | null>(null);
  const [mode, setMode] = useState<ProxyMode>('system');
  const [edits, setEdits] = useState<Partial<Record<FieldKey, string>>>({});
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    fetchProxySettings()
      .then((value) => {
        setConfig(value);
        setMode(value.mode);
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : '读取代理配置失败');
      })
      .finally(() => setBusy(false));
  }, []);

  const save = async () => {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      const updates = mode === 'custom' ? edits : {};
      const value = await saveProxySettings(mode, updates);
      setConfig(value);
      setMode(value.mode);
      setEdits({});
      setMessage('代理设置已保存并应用到当前 Easel');
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : '保存代理配置失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="ai-stack proxy-settings">
      <div>
        <h2><IconGlobe size={18} /> 网络与代理</h2>
        <p>控制 Easel 自身、环境安装、技能和发布子进程使用的代理。保存后当前进程立即生效。</p>
      </div>
      {config ? (
        <>
          <div className="proxy-mode-grid">
            {MODES.map((item) => (
              <button type="button" key={item.value}
                className={`proxy-mode ${mode === item.value ? 'active' : ''}`}
                aria-pressed={mode === item.value}
                onClick={() => { setMode(item.value); setMessage(''); setError(''); }}>
                <strong>{item.label}</strong>
                <small>{item.note}</small>
              </button>
            ))}
          </div>
          {mode === 'custom' ? (
            <div className="proxy-fields">
              {FIELDS.map(([key, valueKey, example]) => {
                const field = config[valueKey as ValueKey];
                return (
                  <label key={key} className="proxy-field">
                    <span>{key}</span>
                    <input
                      className="field"
                      type={key === 'NO_PROXY' ? 'text' : 'password'}
                      value={edits[key] ?? ''}
                      placeholder={field.configured ? `已配置：${field.value}（留空保持）` : example}
                      onChange={(event) => setEdits((current) => ({ ...current, [key]: event.target.value }))}
                      autoComplete="off"
                    />
                  </label>
                );
              })}
              <p className="proxy-hint">
                pip 对 SOCKS 支持取决于运行环境；如果安装失败，优先把 HTTP/HTTPS 代理写成
                <code>http://127.0.0.1:7890</code>。留空字段会保留当前配置。
              </p>
            </div>
          ) : null}
          <div className="proxy-current">
            {mode === 'system' ? '当前值来自系统环境' : mode === 'direct' ? '保存后将清除代理变量' : '留空字段保持已保存值'}
          </div>
          <div className="ai-actions">
            <button className="btn btn-primary" disabled={busy} onClick={() => void save()}>
              {busy ? '应用中…' : '保存并应用'}
            </button>
          </div>
        </>
      ) : busy ? <p>正在读取代理配置…</p> : null}
      {message ? <p className="proxy-success" role="status">{message}</p> : null}
      {error ? <p className="notice-error" role="alert">{error}</p> : null}
    </section>
  );
}
