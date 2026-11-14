import { useEffect, useState } from 'react';
import { aiRequest } from '../../lib/aiWorkspaceApi';
import { runtimeSettings, runtimeSettingsUpdate } from '../../lib/aiRuntimeSettings';
import type { RuntimeSetting } from '../../lib/aiRuntimeSettings';
import { Feedback, Field } from './AiPrimitives';
import { useAiRequest } from './useAiRequest';

export function AiRuntimeSettings() {
  const [fields, setFields] = useState<readonly RuntimeSetting[]>([]);
  const [edits, setEdits] = useState<Readonly<Record<string, string>>>({});
  const [message, setMessage] = useState('');
  const [revision, setRevision] = useState(0);
  const { run, busy, error } = useAiRequest();
  useEffect(() => { void run(async (signal) => {
    const result = runtimeSettings(await aiRequest('/api/generation-settings', signal));
    if (!signal.aborted) { setFields(result); setEdits({}); }
  }); }, [revision, run]);
  function input(field: RuntimeSetting) {
    const label = `${field.label}${field.configured ? '（已配置）' : '（未配置）'}${field.locked ? ' · 由环境锁定' : ''}`;
    const value = edits[field.name] ?? field.value;
    const change = (next: string) => { setEdits((old) => ({ ...old, [field.name]: next })); setMessage(''); };
    return <Field label={label}>{field.name === 'EASEL_DRAFT_MODEL_RATES'
      ? <textarea className="field" value={value} disabled={field.locked || busy} onChange={(event) => change(event.target.value)} spellCheck={false} />
      : <input className="field" type={field.secret ? 'password' : field.name.endsWith('_URL') ? 'url' : 'text'}
        value={value} disabled={field.locked || busy} autoComplete={field.secret ? 'new-password' : 'off'}
        onChange={(event) => change(event.target.value)} />}</Field>;
  }
  return <section className="ai-stack" aria-label="视频处理与运行选项"><h2>视频处理与运行选项</h2>
    <p>智能视频、风格转换、解说和轨道编辑使用视频点播凭据。密钥留空会保留已有配置；环境锁定项不可修改。</p>
    <form className="ai-stack" onSubmit={(event) => { event.preventDefault(); void run(async (signal) => {
      const update = runtimeSettingsUpdate(fields, edits);
      if (!Object.keys(update.values).length) { setMessage('没有需要保存的更改'); return; }
      const result = runtimeSettings(await aiRequest('/api/generation-settings', signal, update));
      if (!signal.aborted) { setFields(result); setEdits({}); setMessage('视频处理与运行选项已保存'); }
    }); }}><div className="ai-grid">{fields.filter((field) => field.name !== 'EASEL_DRAFT_MODEL_RATES').map((field) => <div key={field.name}>{input(field)}</div>)}</div>
      {fields.filter((field) => field.name === 'EASEL_DRAFT_MODEL_RATES').map((field) => <details key={field.name}><summary>高级模型费率</summary>
        <p>按模型名称填写费率数组，包含 amount、currency 和 unit（image、second 或 video）。</p>{input(field)}</details>)}
      <Feedback busy={busy} error={error} />{message && <p role="status">{message}</p>}
      <div className="ai-actions"><button className="btn" disabled={busy || !fields.length || !Object.keys(edits).length}>保存视频处理与运行选项</button>
        <button type="button" className="btn" disabled={busy} onClick={() => setRevision((old) => old + 1)}>重新读取运行选项</button></div>
    </form>
  </section>;
}
