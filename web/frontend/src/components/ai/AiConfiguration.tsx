import { useEffect, useState } from 'react';
import { aiRequest, document, text } from '../../lib/aiWorkspaceApi';
import type { Document } from '../../lib/aiWorkspaceApi';
import { Field, Feedback } from './AiPrimitives';
import { useAiRequest } from './useAiRequest';
import { AiRuntimeSettings } from './AiRuntimeSettings';
import { ModelEditor } from './ModelEditor';

export function AiConfiguration() {
  const { run, busy, error } = useAiRequest();
  const [snapshot, setSnapshot] = useState<Document | null>(null);
  const [config, setConfig] = useState<Document | null>(null);
  const [clear, setClear] = useState<readonly string[]>([]);
  const [message, setMessage] = useState('');
  useEffect(() => { void run(async (signal) => { const value = document(await aiRequest('/api/ai/config', signal)); if (!signal.aborted) { setSnapshot(value); setConfig(document(value.config)); } }); }, [run]);
  function update(section: string, key: string, value: unknown) {
    if (config) { setConfig({ ...config, [section]: { ...document(config[section]), [key]: value } }); setMessage(''); }
  }
  const providers = { ...Object.fromEntries(['openai', 'image', 'video', 'gemini', 'anthropic', 'grok', 'relay', 'dashscope', 'volcengine'].map((name) => [name, { baseUrl: '', apiKey: '' }])), ...(config ? document(config.providers) : {}) };
  const queue = config ? document(config.queue) : {};
  const application = config ? document(config.application) : {};
  return <section className="ai-stack"><Feedback busy={busy} error={error} />
    {!config && !busy && <p>配置暂不可用。需要本机管理员权限。</p>}
    {config && <><h2>提供商</h2><p>密钥留空会保留已有值；勾选清除才会移除。保存后按请求应用。</p>
      {Object.entries(providers).map(([name, raw]) => { const provider = document(raw); const secret = `providers.${name}.apiKey`; return <fieldset className="ai-grid" key={name}><legend>{name}</legend>
        <Field label="服务地址"><input className="field" type="url" value={text(provider.baseUrl)} onChange={(e) => update('providers', name, { ...provider, baseUrl: e.target.value })} /></Field>
        <Field label={`API 密钥${Array.isArray(snapshot?.configuredSecrets) && snapshot.configuredSecrets.includes(secret) ? '（已配置）' : ''}`}><input className="field" type="password" autoComplete="new-password" value={text(provider.apiKey)} onChange={(e) => update('providers', name, { ...provider, apiKey: e.target.value })} /></Field>
        <label><input type="checkbox" checked={clear.includes(secret)} onChange={(e) => setClear((values) => e.target.checked ? [...values, secret] : values.filter((value) => value !== secret))} /> 清除此密钥</label></fieldset>; })}
      <h2>模型目录</h2><ModelEditor models={document(config.models)} onChange={(models) => { setConfig({ ...config, models }); setMessage(''); }} />
      <h2>队列与规划</h2><div className="ai-grid">{Object.entries(queue).map(([name, value]) => <Field key={name} label={name}><input className="field" type="number" min="1" value={text(value)} onChange={(e) => update('queue', name, Number(e.target.value))} /></Field>)}
        <Field label="默认规划模型"><input className="field" value={text(application.plannerModel)} onChange={(e) => update('application', 'plannerModel', e.target.value)} /></Field></div>
      {Array.isArray(snapshot?.locked) && snapshot.locked.length > 0 && <p>由进程环境锁定：{snapshot.locked.map(text).join('、')}</p>}
      <div className="ai-actions"><button className="btn" disabled={busy} onClick={() => { void run(async (signal) => { await aiRequest('/api/ai/config/validate', signal, { config }); if (!signal.aborted) setMessage('配置校验通过'); }); }}>校验配置</button>
        <button className="btn btn-primary" disabled={busy} onClick={() => { void run(async (signal) => {
          const value = document(await aiRequest('/api/ai/config', signal, { config, revision: text(snapshot?.revision), clearSecrets: clear }, 'PUT'));
          if (!signal.aborted) { setSnapshot(value); setConfig(document(value.config)); setClear([]); setMessage('配置已保存并应用'); }
        }); }}>保存并应用</button>
        <button className="btn" disabled={busy} onClick={() => { void run(async (signal) => { await aiRequest('/api/ai/config/restart', signal, {}); if (!signal.aborted) setMessage('运行配置已重新应用'); }); }}>重新应用</button></div>
    </>}{message && <p role="status">{message}</p>}<AiRuntimeSettings /></section>;
}
