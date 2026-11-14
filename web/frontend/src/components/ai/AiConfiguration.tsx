import { useEffect, useState } from 'react';
import { aiRequest, document, text } from '../../lib/aiWorkspaceApi';
import type { Document } from '../../lib/aiWorkspaceApi';
import { Field, Feedback } from './AiPrimitives';
import { useAiRequest } from './useAiRequest';

export function AiConfiguration() {
  const { run, busy, error } = useAiRequest();
  const [snapshot, setSnapshot] = useState<Document | null>(null);
  const [config, setConfig] = useState<Document | null>(null);
  const [message, setMessage] = useState('');
  useEffect(() => { void run(async (signal) => { const value = document(await aiRequest('/api/ai/config', signal)); if (!signal.aborted) { setSnapshot(value); setConfig(document(value.config)); } }); }, [run]);
  function update(section: string, key: string, value: unknown) {
    if (config) { setConfig({ ...config, [section]: { ...document(config[section]), [key]: value } }); setMessage(''); }
  }
  const queue = config ? document(config.queue) : {};
  return <section className="ai-stack"><Feedback busy={busy} error={error} />
    {!config && !busy && <p>配置暂不可用。需要本机管理员权限。</p>}
    {config && <><h2>队列与高级参数</h2>
      <p>模型、供应商、服务地址和密钥统一由「模型配置 · 六个通道」管理；这里只保留队列等运行参数。</p>
      <div className="ai-grid">{Object.entries(queue).map(([name, value]) => <Field key={name} label={name}><input className="field" type="number" min="1" value={text(value)} onChange={(e) => update('queue', name, Number(e.target.value))} /></Field>)}</div>
      {Array.isArray(snapshot?.locked) && snapshot.locked.length > 0 && <p>由进程环境锁定：{snapshot.locked.map(text).join('、')}</p>}
      <div className="ai-actions"><button className="btn" disabled={busy} onClick={() => { void run(async (signal) => { await aiRequest('/api/ai/config/validate', signal, { config }); if (!signal.aborted) setMessage('配置校验通过'); }); }}>校验配置</button>
        <button className="btn btn-primary" disabled={busy} onClick={() => { void run(async (signal) => {
          const value = document(await aiRequest('/api/ai/config', signal, { config, revision: text(snapshot?.revision), clearSecrets: [] }, 'PUT'));
          if (!signal.aborted) { setSnapshot(value); setConfig(document(value.config)); setMessage('配置已保存并应用'); }
        }); }}>保存并应用</button>
        <button className="btn" disabled={busy} onClick={() => { void run(async (signal) => { await aiRequest('/api/ai/config/restart', signal, {}); if (!signal.aborted) setMessage('运行配置已重新应用'); }); }}>重新应用</button></div>
    </>}{message && <p role="status">{message}</p>}</section>;
}
