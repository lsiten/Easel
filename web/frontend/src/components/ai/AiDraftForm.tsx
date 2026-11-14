import { useEffect, useState } from 'react';
import { aiPath, aiRequest, document, rows, text } from '../../lib/aiWorkspaceApi';
import type { Document } from '../../lib/aiWorkspaceApi';
import { draftRequest } from '../../lib/aiDraftRequest';
import type { DraftKind, DraftValues } from '../../lib/aiDraftRequest';
import { modelChoices } from '../../lib/aiPresentation';
import { Feedback, Field } from './AiPrimitives';
import { ModelOption } from './ModelOption';
import { useAiRequest } from './useAiRequest';

const KINDS: readonly { readonly value: DraftKind; readonly label: string }[] = [
  { value: 'image-text', label: '图文批量生成' }, { value: 'video', label: '视频批量生成' }, { value: 'video-url', label: '视频链接转草稿' },
];
const INITIAL: DraftValues = { kind: 'image-text', prompt: '', captionPrompt: '', model: '', plannerModel: '', quantity: 1,
  imageCount: 3, groupId: '', videoUrl: '', ratio: '', imageSize: '', resolution: '', duration: '', images: '', videos: '', audios: '', mediaOnly: false, memory: false, platforms: '' };
export function AiDraftForm({ persona, pricing, groups, onCreated }: {
  readonly persona: string; readonly pricing: Document; readonly groups: readonly Document[]; readonly onCreated: (ids: readonly string[]) => void;
}) {
  const [values, setValues] = useState<DraftValues>(INITIAL);
  const [planners, setPlanners] = useState<readonly Document[]>([]);
  const { run, busy, error } = useAiRequest();
  useEffect(() => { void run(async (signal) => { const result = await aiRequest(aiPath('/api/ai/models/chat', persona), signal); if (!signal.aborted) setPlanners(rows(result).filter((model) => model.channel !== 'anthropic')); }); }, [persona, run]);
  function update<K extends keyof DraftValues>(key: K, value: DraftValues[K]) { setValues((old) => ({ ...old, [key]: value })); }
  function reference(key: 'images' | 'videos' | 'audios', label: string) {
    return <Field label={label}><textarea className="field" value={values[key]} onChange={(event) => update(key, event.target.value)} /></Field>;
  }
  const models = rows(pricing[values.kind === 'video' ? 'videoModels' : 'imageModels']);
  const selected = models.find((model) => model.model === values.model);
  return <form className="ai-stack" onSubmit={(event) => { event.preventDefault(); void run(async (signal) => {
    const request = draftRequest(values);
    const result = document(await aiRequest(aiPath('/api/ai/draft-generation' + request.path, persona), signal, request.payload));
    if (!signal.aborted) onCreated(Array.isArray(result.taskIds) ? result.taskIds.map(text) : []);
  }); }}><div className="ai-grid">
    <Field label="草稿类型"><select className="field" value={values.kind} onChange={(event) => {
      const kind = KINDS.find((item) => item.value === event.target.value)?.value;
      if (kind) setValues((old) => ({ ...old, kind, model: '', ratio: '', resolution: '', imageSize: '', duration: '', mediaOnly: false }));
    }}>{KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></Field>
    <Field label="内容分组"><select className="field" value={values.groupId} onChange={(event) => update('groupId', event.target.value)}><option value="">默认分组</option>{groups.map((group) => <option key={text(group.id)} value={text(group.id)}>{text(group.name)}</option>)}</select></Field>
    <Field label={values.kind === 'video-url' ? '创作要求（可选）' : '创作要求'}><textarea className="field" required={values.kind !== 'video-url'} maxLength={2000} value={values.prompt} onChange={(event) => update('prompt', event.target.value)} /></Field>
    <Field label="文案要求"><textarea className="field" maxLength={2000} value={values.captionPrompt} onChange={(event) => update('captionPrompt', event.target.value)} /></Field>
    <Field label="规划模型"><select className="field" value={values.plannerModel} onChange={(event) => update('plannerModel', event.target.value)}><option value="">使用默认规划模型</option>{planners.map((model) => <option key={text(model.name)} value={text(model.name)}>{text(model.name)}</option>)}</select></Field>
    <Field label="目标平台（英文标识，逗号分隔）"><input className="field" value={values.platforms} onChange={(event) => update('platforms', event.target.value)} /></Field>
    {values.kind === 'video-url' ? <Field label="视频链接"><input className="field" type="url" required value={values.videoUrl} onChange={(event) => update('videoUrl', event.target.value)} /></Field> : <>
      <Field label="生成模型"><select className="field" required value={values.model} onChange={(event) => setValues((old) => ({ ...old, model: event.target.value, ratio: '', imageSize: '', resolution: '', duration: '' }))}><option value="">选择模型</option>{models.map((model) => <option key={text(model.model)} value={text(model.model)}>{text(model.displayName || model.name || model.model)}</option>)}</select></Field>
      <Field label="草稿数量"><input className="field" type="number" min="1" max="10" value={values.quantity} onChange={(event) => update('quantity', Number(event.target.value))} /></Field>
      <ModelOption label="宽高比" choices={modelChoices(selected, 'supportedAspectRatios', 'aspectRatios')} value={values.ratio} onChange={(value) => update('ratio', value)} />
      <Field label="生成结果"><select className="field" value={values.mediaOnly ? 'media' : 'draft'} onChange={(event) => update('mediaOnly', event.target.value === 'media')}><option value="draft">完整草稿（包含文案）</option><option value="media">{values.kind === 'video' ? '仅生成视频' : '仅生成图片'}</option></select></Field>
      {reference('images', '参考图片（每行一个：当前画像素材路径、/api/media/ 地址、HTTPS 地址或图片 data URI）')}
      {values.kind === 'image-text' ? <>
        <Field label="每条图片数"><input className="field" type="number" min="1" max="9" value={values.imageCount} onChange={(event) => update('imageCount', Number(event.target.value))} /></Field>
        <ModelOption label="图片尺寸 / 分辨率" choices={modelChoices(selected, 'resolutions', 'sizes')} value={values.imageSize} onChange={(value) => update('imageSize', value)} />
      </> : <>
        <ModelOption label="视频分辨率" choices={modelChoices(selected, 'resolutions')} value={values.resolution} onChange={(value) => update('resolution', value)} />
        <ModelOption label="视频时长（秒）" choices={modelChoices(selected, 'durations')} value={values.duration} onChange={(value) => update('duration', value)} numeric />
        {reference('videos', '参考视频（每行一个 HTTPS 地址）')}{reference('audios', '参考音频（每行一个 HTTPS 地址）')}
      </>}
    </>}
    <label><input type="checkbox" checked={values.memory} onChange={(event) => update('memory', event.target.checked)} /> 使用画像内容记忆</label>
  </div><Feedback busy={busy} error={error} /><button className="btn btn-primary" disabled={busy}>创建草稿任务</button></form>;
}
