import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { aiPath, aiRequest, document as readDocument, rows, text } from '../../lib/aiWorkspaceApi';
import type { Document } from '../../lib/aiWorkspaceApi';
import { draftRequest } from '../../lib/aiDraftRequest';
import type { DraftKind, DraftValues } from '../../lib/aiDraftRequest';
import { modelChoices } from '../../lib/aiPresentation';
import { Feedback, Field } from './AiPrimitives';
import { ModelOption } from './ModelOption';
import { useAiRequest } from './useAiRequest';
import { IconCheck, IconLayers } from '../icons';

const KINDS: readonly { readonly value: DraftKind; readonly label: string; readonly description: string }[] = [
  { value: 'image-text', label: '图文批量生成', description: '按同一方向生成多条图文草稿。' },
  { value: 'video', label: '视频批量生成', description: '批量规划镜头、视频和配套文案。' },
  { value: 'video-url', label: '视频链接转草稿', description: '从一个已有视频链接生成可编辑草稿。' },
];

const INITIAL: DraftValues = {
  kind: 'image-text', prompt: '', captionPrompt: '', model: '', plannerModel: '', quantity: 1,
  imageCount: 3, groupId: '', videoUrl: '', ratio: '', imageSize: '', resolution: '', duration: '',
  images: '', videos: '', audios: '', mediaOnly: false, memory: false, platforms: '',
};

const filled = (value: string) => Boolean(value.trim());

export function AiDraftForm({ persona, pricing, groups, onCreated }: {
  readonly persona: string;
  readonly pricing: Document;
  readonly groups: readonly Document[];
  readonly onCreated: (ids: readonly string[]) => void;
}) {
  const [values, setValues] = useState<DraftValues>(INITIAL);
  const [planners, setPlanners] = useState<readonly Document[]>([]);
  const pendingKindFocus = useRef<DraftKind | null>(null);
  const { run, busy, error } = useAiRequest();

  useEffect(() => {
    void run(async (signal) => {
      const result = await aiRequest(aiPath('/api/ai/models/chat', persona), signal);
      if (!signal.aborted) setPlanners(rows(result).filter((model) => model.configured !== false));
    });
  }, [persona, run]);

  useEffect(() => {
    if (!pendingKindFocus.current) return;
    const next = pendingKindFocus.current;
    pendingKindFocus.current = null;
    globalThis.document.querySelector<HTMLButtonElement>(`[data-draft-kind="${next}"]`)?.focus();
  }, [values.kind]);

  function update<K extends keyof DraftValues>(key: K, value: DraftValues[K]) {
    setValues((old) => ({ ...old, [key]: value }));
  }

  function changeKind(kind: DraftKind) {
    if (kind === values.kind) return;
    setValues((old) => ({
      ...old, kind, model: '', ratio: '', resolution: '', imageSize: '', duration: '', mediaOnly: false,
    }));
  }

  function handleKindKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0
      : event.key === 'End' ? KINDS.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + KINDS.length) % KINDS.length;
    pendingKindFocus.current = KINDS[next].value;
    changeKind(KINDS[next].value);
  }

  const imageModels = rows(pricing.imageModels);
  const imageEditModels = rows(pricing.imageEditModels);
  const models = values.kind === 'video'
    ? rows(pricing.videoModels)
    : values.kind === 'image-text' && values.images.trim() && imageEditModels.length
      ? imageEditModels
      : imageModels;
  const selected = models.find((model) => model.model === values.model);
  const modelReady = values.kind === 'video-url' || Boolean(selected);
  const needsVision = values.kind === 'video-url' || Boolean(values.images.trim());
  const availablePlanners = planners.filter((model) => {
    const modalities = Array.isArray(model.inputModalities) ? model.inputModalities.map(text) : [];
    return !needsVision || !modalities.length || modalities.includes('image');
  });
  const ready = values.kind === 'video-url'
    ? filled(values.videoUrl)
    : modelReady && filled(values.prompt);
  const kindMeta = KINDS.find((item) => item.value === values.kind) ?? KINDS[0];
  const groupName = groups.find((group) => text(group.id) === values.groupId)?.name || '默认分组';
  const action = values.kind === 'video-url' ? '生成视频草稿' : values.kind === 'video' ? '创建视频草稿' : '创建图文草稿';

  return <form className="creation-form draft-form" aria-busy={busy} onSubmit={(event) => {
    event.preventDefault();
    void run(async (signal) => {
      const request = draftRequest(values);
      const result = readDocument(await aiRequest(aiPath('/api/ai/draft-generation' + request.path, persona), signal, request.payload));
      if (!signal.aborted) onCreated(Array.isArray(result.taskIds) ? result.taskIds.map(text) : []);
    });
  }}>
    <div className="creation-layout draft-layout">
      <div className="creation-form">
        <section className="creation-section" aria-labelledby="draft-kind-heading">
          <div className="creation-section-head">
            <div><h2 id="draft-kind-heading">草稿类型</h2><p>{kindMeta.description}</p></div>
            <span className="creation-mode-name"><IconLayers size={12} /> 批量</span>
          </div>
          <div className="creation-mode-tabs draft-kind-tabs" role="tablist" aria-label="草稿类型">
            {KINDS.map((item) => <button type="button" role="tab" key={item.value}
              data-draft-kind={item.value}
              aria-selected={values.kind === item.value} tabIndex={values.kind === item.value ? 0 : -1}
              className={`subnav-tab creation-mode-tab ${values.kind === item.value ? 'active' : ''}`}
              onClick={() => changeKind(item.value)} onKeyDown={(event) => handleKindKey(event, KINDS.indexOf(item))}>{item.label}</button>)}
          </div>
          <div className="creation-field-grid draft-basic-fields">
            <Field label="内容分组">
              <select className="field" value={values.groupId} onChange={(event) => update('groupId', event.target.value)}>
                <option value="">默认分组</option>
                {groups.map((group) => <option key={text(group.id)} value={text(group.id)}>{text(group.name)}</option>)}
              </select>
            </Field>
            <Field label="目标平台" hint="使用英文标识，多个平台用逗号分隔。">
              <input className="field" value={values.platforms} placeholder="xiaohongshu, douyin"
                onChange={(event) => update('platforms', event.target.value)} />
            </Field>
          </div>
        </section>

        <section className="creation-section" aria-labelledby="draft-content-heading">
          <div className="creation-section-head">
            <div><h2 id="draft-content-heading">创作内容</h2><p>说明批量草稿的共同方向和文案要求。</p></div>
          </div>
          <div className="draft-copy-fields">
            <Field label="创作要求" required={values.kind !== 'video-url'}
              hint={values.kind === 'video-url' ? '可选。留空时根据原视频内容提取。' : '描述主题、受众、重点信息和表达方向。'}>
              <textarea className="field" required={values.kind !== 'video-url'} maxLength={2000}
                value={values.prompt} onChange={(event) => update('prompt', event.target.value)} />
            </Field>
            <Field label="文案要求" hint="可选。补充标题、正文、标签或语气要求。">
              <textarea className="field" maxLength={2000} value={values.captionPrompt}
                onChange={(event) => update('captionPrompt', event.target.value)} />
            </Field>
          </div>
          {values.kind === 'video-url' && <Field label="视频链接" required>
            <input className="field" type="url" required value={values.videoUrl}
              placeholder="https://example.com/video.mp4" onChange={(event) => update('videoUrl', event.target.value)} />
          </Field>}
        </section>

        {values.kind !== 'video-url' && <section className="creation-section" aria-labelledby="draft-settings-heading">
          <div className="creation-section-head">
            <div><h2 id="draft-settings-heading">生成设置</h2><p>控制批量数量、生成模型和输出规格。</p></div>
          </div>
          <div className="creation-field-grid">
            <Field label="规划模型（对话通道）">
              <select className="field" value={values.plannerModel} onChange={(event) => update('plannerModel', event.target.value)}>
                <option value="">跟随对话通道默认模型</option>
                {availablePlanners.map((model) => <option key={text(model.name)} value={text(model.name)}>{text(model.name)}</option>)}
              </select>
            </Field>
            <Field label={values.kind === 'image-text' && values.images.trim() ? '编辑模型' : '生成模型'} required>
              <select className="field" required value={values.model} onChange={(event) => {
                setValues((old) => ({ ...old, model: event.target.value, ratio: '', imageSize: '', resolution: '', duration: '' }));
              }}>
                <option value="">选择模型</option>
                {models.map((model) => <option key={text(model.model)} value={text(model.model)}>{text(model.displayName || model.name || model.model)}</option>)}
              </select>
            </Field>
            <Field label="草稿数量">
              <input className="field" type="number" min="1" max="10" disabled={!selected} value={values.quantity}
                onChange={(event) => update('quantity', Number(event.target.value))} />
            </Field>
            <ModelOption label="宽高比" choices={modelChoices(selected, 'supportedAspectRatios', 'aspectRatios')}
              value={values.ratio} disabled={!selected} onChange={(value) => update('ratio', value)} />
            <Field label="生成结果">
              <select className="field" value={values.mediaOnly ? 'media' : 'draft'} onChange={(event) => update('mediaOnly', event.target.value === 'media')}>
                <option value="draft">完整草稿（包含文案）</option>
                <option value="media">{values.kind === 'video' ? '仅生成视频' : '仅生成图片'}</option>
              </select>
            </Field>
            {values.kind === 'image-text' ? <>
              <Field label="每条图片数">
                <input className="field" type="number" min="1" max="9" disabled={!selected} value={values.imageCount}
                  onChange={(event) => update('imageCount', Number(event.target.value))} />
              </Field>
              <ModelOption label="图片尺寸 / 分辨率" choices={modelChoices(selected, 'resolutions', 'sizes')}
                value={values.imageSize} disabled={!selected} onChange={(value) => update('imageSize', value)} />
            </> : <>
              <ModelOption label="视频分辨率" choices={modelChoices(selected, 'resolutions')}
                value={values.resolution} disabled={!selected} onChange={(value) => update('resolution', value)} />
              <ModelOption label="视频时长（秒）" choices={modelChoices(selected, 'durations')}
                value={values.duration} numeric disabled={!selected} onChange={(value) => update('duration', value)} />
            </>}
          </div>
        </section>}

        {values.kind !== 'video-url' && <section className="creation-section" aria-labelledby="draft-references-heading">
          <div className="creation-section-head">
            <div><h2 id="draft-references-heading">参考素材</h2><p>可选。每行填写一个当前画像素材路径或 HTTPS 地址。</p></div>
          </div>
          <div className="draft-reference-fields">
            <Field label="参考图片" hint="每行一个素材路径、媒体地址、HTTPS 地址或图片 data URI。">
              <textarea className="field" value={values.images} onChange={(event) => update('images', event.target.value)} />
            </Field>
            {values.kind === 'video' && <details className="creation-details">
              <summary>视频与音频参考</summary>
              <div className="creation-field-grid">
                <Field label="参考视频"><textarea className="field" value={values.videos} onChange={(event) => update('videos', event.target.value)} /></Field>
                <Field label="参考音频"><textarea className="field" value={values.audios} onChange={(event) => update('audios', event.target.value)} /></Field>
              </div>
            </details>}
          </div>
        </section>}

        <Feedback busy={false} error={error} />
        <div className="creation-action-bar">
          <div className="creation-readiness" role="status" aria-live="polite">
            <span className={`creation-readiness-icon ${ready ? 'ready' : ''}`} aria-hidden>{ready ? <IconCheck size={13} /> : null}</span>
            <span>{ready ? '信息已完整，可以创建任务' : '填写创作要求和生成模型后即可创建'}</span>
          </div>
          <div className="creation-actions">
            <label className="draft-memory-toggle">
              <input type="checkbox" checked={values.memory} onChange={(event) => update('memory', event.target.checked)} />
              使用画像内容记忆
            </label>
            <button type="submit" className="btn btn-primary creation-submit" disabled={busy} aria-busy={busy}>
              <span className="creation-submit-content">{busy && <span className="creation-spinner" aria-hidden />}{busy ? '正在创建' : action}</span>
            </button>
          </div>
        </div>
      </div>

      <aside className="creation-summary draft-summary" aria-labelledby="draft-summary-heading">
        <h2 id="draft-summary-heading">本次批量任务</h2>
        <dl>
          <div><dt>类型</dt><dd>{kindMeta.label}</dd></div>
          <div><dt>分组</dt><dd>{text(groupName)}</dd></div>
          <div><dt>规划</dt><dd>{values.plannerModel || '对话通道默认模型'}</dd></div>
          <div><dt>生成</dt><dd>{values.kind === 'video-url' ? '单个草稿' : `${values.model || '尚未选择'} · ${values.quantity} 条`}</dd></div>
          <div><dt>平台</dt><dd>{values.platforms || '跟随默认发布设置'}</dd></div>
        </dl>
        <p>创建后会显示任务进度。批量数量、参考素材和生成结果都会按当前选择提交。</p>
      </aside>
    </div>
  </form>;
}
