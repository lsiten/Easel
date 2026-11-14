import { useState } from 'react';
import { document, rows, text } from '../../lib/aiWorkspaceApi';
import type { Document } from '../../lib/aiWorkspaceApi';
import { Field } from './AiPrimitives';

const GROUPS = ['chat', 'image-generation', 'image-edit', 'video'] as const;
type Group = typeof GROUPS[number];
export function ModelEditor({ models, onChange }: { readonly models: Document; readonly onChange: (models: Document) => void }) {
  const [group, setGroup] = useState<Group>('image-generation');
  const list = group === 'chat' ? rows(models.chat) : group === 'video' ? rows(document(models.video).generation) : rows(document(models.image)[group === 'image-edit' ? 'edit' : 'generation']);
  function save(next: readonly Document[]) {
    if (group === 'chat') onChange({ ...models, chat: next });
    else if (group === 'video') onChange({ ...models, video: { ...document(models.video), generation: next } });
    else onChange({ ...models, image: { ...document(models.image), [group === 'image-edit' ? 'edit' : 'generation']: next } });
  }
  return <div className="ai-stack"><div className="ai-actions">{GROUPS.map((value) => <button className="btn" key={value} aria-pressed={group === value} onClick={() => setGroup(value)}>{value}</button>)}</div>
    {list.map((model, index) => <ModelRow key={`${group}-${index}`} group={group} model={model} onChange={(next) => save(list.map((item, position) => position === index ? next : item))} onDelete={() => save(list.filter((_, position) => position !== index))} />)}
    <button className="btn" onClick={() => save([...list, { name: '', channel: 'openai' }])}>添加模型</button></div>;
}
function ModelRow({ group, model, onChange, onDelete }: { readonly group: Group; readonly model: Document; readonly onChange: (model: Document) => void; readonly onDelete: () => void }) {
  const arrays = group.startsWith('image') ? ['sizes', 'qualities', 'styles', 'supportedAspectRatios', 'resolutions', 'inputModalities', 'outputModalities']
    : group === 'video' ? ['modes', 'resolutions', 'aspectRatios', 'durations'] : ['inputModalities', 'outputModalities', 'scenes'];
  return <fieldset className="ai-grid"><legend>{text(model.name) || '新模型'}</legend>
    <Field label="模型名称"><input className="field" value={text(model.name)} onChange={(e) => onChange({ ...model, name: e.target.value })} /></Field>
    <Field label="提供商通道"><select className="field" value={text(model.channel)} onChange={(e) => onChange({ ...model, channel: e.target.value })}>{(group.startsWith('image') ? ['openai', 'agnes', 'gemini'] : group === 'video' ? ['openai', 'volcengine', 'grok', 'dashscope', 'relay'] : ['openai', 'anthropic']).map((value) => <option key={value}>{value}</option>)}</select></Field>
    <Field label="描述"><input className="field" value={text(model.description)} onChange={(e) => onChange({ ...model, description: e.target.value })} /></Field>
    {group !== 'chat' && <><Field label="实际模型名称"><input className="field" value={text(model.runtimeModel)} onChange={(e) => onChange({ ...model, runtimeModel: e.target.value })} /></Field>
      <Field label="队列优先级（数值越小越优先）"><input className="field" type="number" min="1" value={text(model.queuePriority) || '1'} onChange={(e) => onChange({ ...model, queuePriority: Number(e.target.value) })} /></Field></>}
    {arrays.map((key) => <Field key={key} label={`${key}（逗号分隔）`}><input className="field" value={Array.isArray(model[key]) ? model[key].map(text).join(',') : ''} onChange={(e) => onChange({ ...model, [key]: e.target.value.split(',').filter(Boolean).map((value) => key === 'durations' ? Number(value) : value.trim()) })} /></Field>)}
    <button className="btn" onClick={onDelete}>移除模型</button></fieldset>;
}
