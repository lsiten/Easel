import { useEffect, useState } from 'react';
import { document, rows, text } from '../../lib/aiWorkspaceApi';
import type { Document } from '../../lib/aiWorkspaceApi';
import { Field } from './AiPrimitives';

export type ConfigChannel = 'chat' | 'transcribe' | 'speech' | 'image' | 'video' | 'music';
type CatalogChannel = 'chat' | 'image' | 'video';
type Group = 'chat' | 'image-generation' | 'image-edit' | 'video';

const GROUPS: Readonly<Record<CatalogChannel, readonly Group[]>> = {
  chat: ['chat'],
  image: ['image-generation', 'image-edit'],
  video: ['video'],
};

const GROUP_LABELS: Readonly<Record<Group, string>> = {
  chat: '对话模型',
  'image-generation': '生图模型',
  'image-edit': '改图模型',
  video: '视频模型',
};

const CHANNELS: Readonly<Record<Group, readonly string[]>> = {
  chat: ['openai', 'anthropic'],
  'image-generation': ['openai', 'agnes', 'gemini'],
  'image-edit': ['openai', 'agnes', 'gemini'],
  video: ['openai', 'volcengine', 'grok', 'dashscope', 'relay'],
};

const PROVIDER_SLOT: Readonly<Record<Group, Readonly<Record<string, string>>>> = {
  chat: { openai: 'openai', anthropic: 'anthropic' },
  'image-generation': { openai: 'openai', agnes: 'openai', gemini: 'gemini' },
  'image-edit': { openai: 'openai', agnes: 'openai', gemini: 'gemini' },
  video: {
    openai: 'openai-compatible',
    volcengine: 'ark',
    grok: 'grok',
    dashscope: 'dashscope',
    relay: 'relay',
  },
};

function catalogChannel(channel: ConfigChannel): CatalogChannel | null {
  return channel === 'chat' || channel === 'image' || channel === 'video' ? channel : null;
}

function groupChannels(group: Group, providerSlot: string): readonly string[] {
  return CHANNELS[group].filter((channel) => PROVIDER_SLOT[group][channel] === providerSlot);
}

function modelsFor(config: Document, group: Group): readonly Document[] {
  if (group === 'chat') return rows(config.chat);
  if (group === 'video') return rows(document(config.video).generation);
  return rows(document(config.image)[group === 'image-edit' ? 'edit' : 'generation']);
}

function replaceModels(config: Document, group: Group, models: readonly Document[]): Document {
  if (group === 'chat') return { ...config, chat: models };
  if (group === 'video') return { ...config, video: { ...document(config.video), generation: models } };
  return {
    ...config,
    image: {
      ...document(config.image),
      [group === 'image-edit' ? 'edit' : 'generation']: models,
    },
  };
}

export function ProviderModelCapabilities({
  channel,
  providerSlot,
  providerName,
  modelName,
  config,
  onChange,
}: {
  readonly channel: ConfigChannel;
  readonly providerSlot: string;
  readonly providerName: string;
  readonly modelName: string;
  readonly config: Document | null;
  readonly onChange: (config: Document) => void;
}) {
  const catalog = catalogChannel(channel);
  const groups = catalog ? GROUPS[catalog] : [];
  const firstGroup = groups[0] || 'chat';
  const [group, setGroup] = useState<Group>(groups[0] || 'chat');

  useEffect(() => {
    setGroup(firstGroup);
  }, [firstGroup]);

  if (!catalog) {
    return <section className="provider-capabilities" aria-label={`${providerName} 模型能力`}>
      <div className="provider-capability-head">
        <strong>模型能力</strong>
        <span>与上方 provider 使用同一份配置</span>
      </div>
      <div className="provider-capability-summary">
        <span>{modelName || '使用该 provider 的模型字段'}</span>
        <b>当前通道默认能力</b>
      </div>
    </section>;
  }

  if (!config) {
    return <div className="provider-capability-loading">正在读取该 provider 的模型能力…</div>;
  }

  const allModels = modelsFor(document(config.models), group);
  const indexed = allModels
    .map((model, index) => ({ model, index }))
    .filter(({ model }) => PROVIDER_SLOT[group][text(model.channel)] === providerSlot);
  const availableChannels = groupChannels(group, providerSlot);

  function updateModel(index: number, next: Document) {
    if (!config) return;
    onChange({
      ...config,
      models: replaceModels(
        document(config.models),
        group,
        allModels.map((item, position) => position === index ? next : item),
      ),
    });
  }

  function deleteModel(index: number) {
    if (!config) return;
    onChange({
      ...config,
      models: replaceModels(
        document(config.models),
        group,
        allModels.filter((_, position) => position !== index),
      ),
    });
  }

  function addModel() {
    if (!config || !availableChannels.length) return;
    onChange({
      ...config,
      models: replaceModels(
        document(config.models),
        group,
        [...allModels, { name: '', channel: availableChannels[0] }],
      ),
    });
  }

  return <section className="provider-capabilities" aria-label={`${providerName} 模型能力`}>
    <div className="provider-capability-head">
      <strong>模型与能力</strong>
      <span>归属于上方同一条 provider 配置；可用地址、密钥和路由顺序共享。</span>
    </div>
    {groups.length > 1 && <div className="provider-capability-tabs">
      {groups.map((value) => <button type="button" className={`btn btn-sm ${group === value ? 'active' : ''}`}
        aria-pressed={group === value} key={value} onClick={() => setGroup(value)}>
        {GROUP_LABELS[value]}
      </button>)}
    </div>}
    {indexed.map(({ model, index }) => <ModelDefinition key={`${group}-${index}`} group={group}
      model={model} channels={availableChannels}
      onChange={(next) => updateModel(index, next)}
      onDelete={() => deleteModel(index)} />)}
    {!indexed.length && <p className="provider-capability-empty">此 provider 暂未声明 {GROUP_LABELS[group]}；添加后会复用这条 provider 配置。</p>}
    <button type="button" className="btn btn-sm" disabled={!availableChannels.length} onClick={addModel}>
      添加{GROUP_LABELS[group]}
    </button>
  </section>;
}

function ModelDefinition({ group, model, channels, onChange, onDelete }: {
  readonly group: Group;
  readonly model: Document;
  readonly channels: readonly string[];
  readonly onChange: (model: Document) => void;
  readonly onDelete: () => void;
}) {
  const arrays = group.startsWith('image')
    ? ['sizes', 'qualities', 'styles', 'supportedAspectRatios', 'resolutions', 'inputModalities', 'outputModalities']
    : group === 'video'
      ? ['modes', 'resolutions', 'aspectRatios', 'durations']
      : ['inputModalities', 'outputModalities', 'scenes'];
  return <fieldset className="ai-grid channel-model-definition">
    <legend>{text(model.name) || '新模型'}</legend>
    <Field label="模型名称"><input className="field" value={text(model.name)} maxLength={200}
      onChange={(event) => onChange({ ...model, name: event.target.value })} /></Field>
    <Field label="使用同 provider 的实现">
      <select className="field" value={text(model.channel)}
        onChange={(event) => onChange({ ...model, channel: event.target.value })}>
        {channels.map((value) => <option key={value} value={value}>{value}</option>)}
      </select>
    </Field>
    <Field label="描述"><input className="field" value={text(model.description)}
      onChange={(event) => onChange({ ...model, description: event.target.value })} /></Field>
    {group !== 'chat' && <>
      <Field label="实际模型名称" hint="留空时使用上面的模型名称。"><input className="field" value={text(model.runtimeModel)}
        onChange={(event) => onChange({ ...model, runtimeModel: event.target.value })} /></Field>
      <Field label="队列优先级" hint="数值越小越优先。"><input className="field" type="number" min="1"
        value={text(model.queuePriority) || '1'}
        onChange={(event) => onChange({ ...model, queuePriority: Number(event.target.value) })} /></Field>
    </>}
    {group.startsWith('image') && <Field label="最大参考图数量"><input className="field" type="number" min="1" max="16"
      value={text(model.maxInputImages)}
      onChange={(event) => onChange({ ...model, maxInputImages: event.target.value ? Number(event.target.value) : null })} /></Field>}
    {arrays.map((key) => <Field key={key} label={`${key}（逗号分隔）`}>
      <input className="field" value={Array.isArray(model[key]) ? model[key].map(text).join(',') : ''}
        onChange={(event) => onChange({
          ...model,
          [key]: event.target.value.split(',').filter(Boolean).map((value) => key === 'durations' ? Number(value) : value.trim()),
        })} />
    </Field>)}
    <button type="button" className="btn" disabled={!text(model.name)} onClick={onDelete}>移除模型</button>
  </fieldset>;
}
