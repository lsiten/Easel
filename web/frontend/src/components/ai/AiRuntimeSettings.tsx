import { useEffect, useState } from 'react';
import { aiRequest } from '../../lib/aiWorkspaceApi';
import { runtimeSettings, runtimeSettingsUpdate } from '../../lib/aiRuntimeSettings';
import type { RuntimeSetting } from '../../lib/aiRuntimeSettings';
import { Feedback, Field } from './AiPrimitives';
import { useAiRequest } from './useAiRequest';

export function AiRuntimeSettings({ names, title = '通道高级运行项', description = '这些参数归属于当前模型通道，不需要在其他设置页重复配置。' }: {
  readonly names: readonly string[];
  readonly title?: string;
  readonly description?: string;
}) {
  const [fields, setFields] = useState<readonly RuntimeSetting[]>([]);
  const [edits, setEdits] = useState<Readonly<Record<string, string>>>({});
  const [message, setMessage] = useState('');
  const [revision, setRevision] = useState(0);
  const { run, busy, error } = useAiRequest();
  useEffect(() => { void run(async (signal) => {
    const result = runtimeSettings(await aiRequest('/api/generation-settings', signal), names);
    if (!signal.aborted) { setFields(result); setEdits({}); }
  }); }, [names, revision, run]);
  function input(field: RuntimeSetting) {
    const label = `${field.label}${field.configured ? '（已配置）' : '（未配置）'}${field.locked ? ' · 由环境锁定' : ''}`;
    const value = edits[field.name] ?? field.value;
    const change = (next: string) => { setEdits((old) => ({ ...old, [field.name]: next })); setMessage(''); };
    const hint = [field.description, field.default && !field.value ? `默认：${field.default}` : ''].filter(Boolean).join(' ');
    return <Field label={label} hint={hint || undefined} required={field.required}>{field.name === 'EASEL_DRAFT_MODEL_RATES'
      ? <textarea className="field" value={value} disabled={field.locked || busy} onChange={(event) => change(event.target.value)} spellCheck={false} />
      : <input className="field" type={field.secret ? 'password' : field.name.endsWith('_URL') ? 'url' : 'text'}
        value={value} disabled={field.locked || busy} autoComplete={field.secret ? 'new-password' : 'off'}
        onChange={(event) => change(event.target.value)} />}</Field>;
  }
  if (!fields.length) return null;
  const standard = fields.filter((field) => field.name !== 'EASEL_DRAFT_MODEL_RATES');
  const grouped = new Map<string, RuntimeSetting[]>();
  for (const field of standard) {
    grouped.set(field.section, [...(grouped.get(field.section) || []), field]);
  }
  return <section className="ai-stack channel-runtime-settings" aria-label={title}><h3>{title}</h3>
    <p>{description} 标 * 的字段只在对应能力启用时需要；密钥留空会保留已有值。</p>
    <form className="ai-stack" onSubmit={(event) => { event.preventDefault(); void run(async (signal) => {
      const update = runtimeSettingsUpdate(fields, edits);
      if (!Object.keys(update.values).length) { setMessage('没有需要保存的更改'); return; }
      const result = runtimeSettings(await aiRequest('/api/generation-settings', signal, update), names);
      if (!signal.aborted) { setFields(result); setEdits({}); setMessage(`${title}已保存`); }
    }); }}>{[...grouped].map(([section, values]) => <section key={section} className="runtime-setting-group">
      <h4>{section}</h4><div className="ai-grid">{values.map((field) => <div key={field.name}>{input(field)}</div>)}</div>
    </section>)}
      {fields.filter((field) => field.name === 'EASEL_DRAFT_MODEL_RATES').map((field) => <details key={field.name}><summary>高级模型费率</summary>
        <p>{field.description || '按模型名称填写费率数组，包含 amount、currency 和 unit。'} 它只影响预估，不改变真实计费。</p>{input(field)}</details>)}
      <Feedback busy={busy} error={error} />{message && <p role="status">{message}</p>}
      <div className="ai-actions"><button className="btn" disabled={busy || !fields.length || !Object.keys(edits).length}>保存{title}</button>
        <button type="button" className="btn" disabled={busy} onClick={() => setRevision((old) => old + 1)}>重新读取运行选项</button></div>
    </form>
  </section>;
}
