import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { aiPath, aiRequest, rows, text } from '../../lib/aiWorkspaceApi';
import { loadAiQuery } from '../../lib/aiQuery';
import { channelError } from '../../lib/channelsApi';
import type { Document } from '../../lib/aiWorkspaceApi';
import { creationRequest, creationReceipt, CREATION_MODES } from '../../lib/aiCreation';
import type { CreationMode, CreationValues } from '../../lib/aiCreation';
import { Feedback, Field } from './AiPrimitives';
import { JobProgress } from './AiHistory';
import { ModelOption } from './ModelOption';
import { modelChoices } from '../../lib/aiPresentation';
import { useAiRequest } from './useAiRequest';
import { IconCheck } from '../icons';

const MODE_META = {
  image: { description: '从文字描述生成一组图片。', action: '生成图片', promptHint: '描述主体、场景、构图、光线和期望风格。' },
  edit: { description: '用文字修改已有图片，可加入遮罩。', action: '编辑图片', promptHint: '说明要保留和修改的部分，例如“保留人物姿势，把背景改为夜景”。' },
  video: { description: '从文字和参考素材生成视频。', action: '生成视频', promptHint: '描述镜头运动、主体动作、场景和节奏。' },
  aideo: { description: '对已有视频执行翻译、擦除、高光等技能。', action: '创建智能视频任务', promptHint: '不选择技能时，在这里描述希望执行的处理。' },
  style: { description: '把已有视频转换为指定视觉风格。', action: '转换视频风格', promptHint: '' },
  recap: { description: '为短剧或多段视频生成解说。', action: '生成短剧解说', promptHint: '可填写既定解说文案；留空则由系统生成。' },
  'direct-edit': { description: '用画布和时间轨道重新组合视频。', action: '创建轨道任务', promptHint: '' },
  character: { description: '从视频中截取角色片段。', action: '创建角色片段', promptHint: '可选：描述需要关注的角色或动作。' },
  remix: { description: '基于已有视频任务重新生成版本。', action: '重混视频', promptHint: '描述希望改变的镜头、节奏或风格。' },
} satisfies Record<CreationMode, { description: string; action: string; promptHint: string }>;

const usesModel = (mode: CreationMode) => mode === 'image' || mode === 'edit' || mode === 'video';
const hasPrompt = (mode: CreationMode) => mode !== 'style' && mode !== 'direct-edit';
const promptRequired = (mode: CreationMode) => !['aideo', 'recap', 'character', 'remix'].includes(mode);
const filled = (value?: string) => Boolean(value?.trim());

function isReady(mode: CreationMode, values: CreationValues): boolean {
  const model = !usesModel(mode) || filled(values.model);
  switch (mode) {
    case 'image': return model && filled(values.prompt);
    case 'edit': return model && filled(values.prompt) && filled(values.images);
    case 'video': return model && filled(values.prompt);
    case 'aideo': return filled(values.inputs) && (filled(values.skill) || filled(values.prompt));
    case 'style': return filled(values.video);
    case 'recap': return filled(values.inputs);
    case 'direct-edit': return filled(values.track) || filled(values.video);
    case 'character': return filled(values.prompt) && (filled(values.video) || filled(values.taskId));
    case 'remix': return filled(values.taskId);
  }
}

export function AiCreation({ persona }: { readonly persona: string }) {
  const [mode, setMode] = useState<CreationMode>('image');
  const [values, setValues] = useState<CreationValues>({});
  const [models, setModels] = useState<readonly Document[]>([]);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [detail, setDetail] = useState('');
  const [key, setKey] = useState(() => crypto.randomUUID());
  const pendingModeFocus = useRef<CreationMode | null>(null);
  const { run, busy, error } = useAiRequest();
  const catalog = mode === 'video' ? 'video/generation' : `image/${mode === 'edit' ? 'edit' : 'generation'}`;

  useEffect(() => {
    setModels([]);
    setCatalogError('');
    if (!usesModel(mode)) { setCatalogBusy(false); return; }
    const controller = new AbortController();
    setCatalogBusy(true);
    void loadAiQuery(aiPath('/api/ai/models/' + catalog, persona), controller.signal, rows).then((result) => {
      if (result !== null && !controller.signal.aborted) setModels(result);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setCatalogError(channelError(failure));
    }).finally(() => {
      if (!controller.signal.aborted) setCatalogBusy(false);
    });
    return () => controller.abort();
  }, [persona, catalog, mode, catalogRevision]);

  useEffect(() => {
    if (!pendingModeFocus.current) return;
    const next = pendingModeFocus.current;
    pendingModeFocus.current = null;
    document.querySelector<HTMLButtonElement>(`[data-creation-mode="${next}"]`)?.focus();
  }, [mode]);

  function update(name: string, value: string) {
    setValues((old) => ({ ...old, [name]: value }));
  }

  function field(name: string, label: string, required = false, type = 'text', disabled = false) {
    return <Field key={name} label={label} required={required}>
      <input className="field" disabled={disabled} required={required} type={type}
        value={values[name] || ''} onChange={(event) => update(name, event.target.value)} />
    </Field>;
  }

  function area(name: string, label: string, required = false, disabled = false, hint?: string) {
    return <Field key={name} label={label} required={required} hint={hint}>
      <textarea className="field" disabled={disabled} required={required} maxLength={2000}
        value={values[name] || ''} onChange={(event) => update(name, event.target.value)} />
    </Field>;
  }

  const selected = models.find((model) => model.name === values.model);
  const ready = isReady(mode, values);
  const promptLength = (values.prompt || '').length;

  function option(name: string, label: string, keys: readonly string[], numeric = false) {
    return <ModelOption label={label} choices={modelChoices(selected, ...keys)} value={values[name] || ''}
      numeric={numeric} disabled={!selected} onChange={(value) => update(name, value)} />;
  }

  function switchMode(value: CreationMode) {
    if (value === mode) return;
    setModels([]);
    setMode(value);
    setValues({});
    setDetail('');
    setKey(crypto.randomUUID());
  }

  function handleModeKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0
      : event.key === 'End' ? CREATION_MODES.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + CREATION_MODES.length) % CREATION_MODES.length;
    pendingModeFocus.current = CREATION_MODES[next][0];
    switchMode(CREATION_MODES[next][0]);
  }

  const sourceFields = <Fragment>
    {mode === 'edit' && <>{area('images', '参考图片地址', true, false, '每行一个地址，可填写素材路径、媒体地址或 HTTPS 地址。')}{field('mask', '遮罩图片地址')}</>}
    {mode === 'video' && <>
      {area('images', '参考图片地址', false, false, '每行一个，用于引导主体或画面风格。')}
      {field('imageTail', '尾帧图片地址')}
      <details className="creation-details"><summary>更多参考素材</summary><div className="creation-field-grid">
        {area('videos', '参考视频地址', false, false, '每行一个 HTTPS 地址。')}
        {area('audios', '参考音频地址', false, false, '每行一个 HTTPS 地址。')}
      </div></details>
    </>}
    {mode === 'aideo' && <>
      {area('inputs', '视频输入地址或 VID', true, false, '每行一个视频地址或 VID。')}
      {field('space', '视频空间')}
    </>}
    {mode === 'style' && field('video', '视频输入地址', true)}
    {mode === 'recap' && area('inputs', '视频 VID', true, false, '每行一个 VID。')}
    {mode === 'direct-edit' && <>
      {field('video', '视频来源（地址或 VID）', !values.track)}
      <details className="creation-details"><summary>高级轨道编辑</summary>
        {area('track', 'Track JSON（覆盖单轨道设置）')}
      </details>
    </>}
    {mode === 'character' && <>
      {field('video', '视频地址', !values.taskId)}
      {field('taskId', '已有视频任务 ID')}
    </>}
    {mode === 'remix' && field('taskId', '原视频任务 ID', true)}
  </Fragment>;

  const settingsFields = <div className="creation-field-grid">
    {usesModel(mode) && <>
      <Field label="模型" required>
        <select className="field" required disabled={catalogBusy} value={values.model || ''} onChange={(event) => {
          const model = models.find((item) => item.name === event.target.value);
          setValues((old) => ({
            ...old, model: event.target.value, channel: text(model?.channel),
            ratio: '', resolution: '', imageSize: '', size: '', quality: '', style: '', duration: '', mode: '',
          }));
        }}>
          <option value="">{catalogBusy ? '正在读取模型配置…' : '选择已配置模型'}</option>
          {models.map((model) => <option key={text(model.name)} value={text(model.name)}>{text(model.name)} · {text(model.channel)}</option>)}
        </select>
      </Field>
      {selected && <div className="creation-model-note">
        <span className={`creation-status-dot ${selected.configured === false ? 'warn' : 'ok'}`} aria-hidden />
        <span>{text(selected.description) || '已读取模型能力，未填写的参数会使用模型默认值。'}</span>
        {selected.configured === false && <strong>提供商尚未配置</strong>}
      </div>}
    </>}
    {['image', 'edit'].includes(mode) && <>
      {option('size', '图片尺寸', ['sizes'])}
      {option('ratio', '宽高比', ['supportedAspectRatios', 'aspectRatios'])}
      {values.channel === 'gemini' && option('imageSize', 'Gemini 原生分辨率', ['resolutions'])}
      {mode === 'image' && <>
        {field('quantity', '张数（1–10）', false, 'number', !selected)}
        {option('quality', '画质', ['qualities'])}
        {option('style', '风格', ['styles'])}
      </>}
    </>}
    {mode === 'video' && <>
      {option('mode', '生成模式', ['modes'])}
      {option('duration', '时长（秒）', ['durations'], true)}
      {option('resolution', '分辨率', ['resolutions'])}
      {option('ratio', '宽高比', ['aspectRatios', 'supportedAspectRatios'])}
      {field('groupId', '内容分组 ID', false, 'text', !selected)}
    </>}
    {mode === 'aideo' && <>
      <Field label="技能">
        <select className="field" value={values.skill || ''} onChange={(event) => update('skill', event.target.value)}>
          {['', 'AITranslation', 'Erase', 'Highlight', 'VCreative', 'Vision'].map((value) =>
            <option key={value} value={value}>{value || '按创作要求执行'}</option>)}
        </select>
      </Field>
      <details className="creation-details"><summary>高级技能参数</summary>{area('params', '技能参数 JSON')}</details>
    </>}
    {mode === 'style' && <>
      {field('style', '目标风格')}
      {field('resolution', '分辨率')}
    </>}
    {mode === 'recap' && <>
      {field('style', '解说风格')}
      {field('speed', '语速（0.5–2）', false, 'number')}
      {field('pause', '停顿毫秒', false, 'number')}
      {field('voice', '声音类型')}
      {field('appId', '语音应用 ID')}
      {field('cluster', '语音集群')}
    </>}
    {mode === 'direct-edit' && <>
      {field('start', '开始秒', false, 'number')}
      {field('end', '结束秒', false, 'number')}
      {field('width', '画布宽度', false, 'number')}
      {field('height', '画布高度', false, 'number')}
      {field('space', '输出空间')}
    </>}
    {mode === 'character' && <>
      {field('start', '片段开始秒', false, 'number')}
      {field('end', '片段结束秒（最长 3 秒）', false, 'number')}
    </>}
  </div>;

  const configured = [
    usesModel(mode) && values.model ? `模型 ${values.model}` : '',
    values.size ? `尺寸 ${values.size}` : '',
    values.ratio ? `比例 ${values.ratio}` : '',
    values.quantity ? `${values.quantity} 张` : '',
    values.duration ? `${values.duration} 秒` : '',
    values.resolution ? `分辨率 ${values.resolution}` : '',
  ].filter(Boolean);

  return <section className="ai-workspace ai-stack creation-workspace creation-generate-page" aria-label="生成内容">
    <div className="creation-mode-shell">
      <div className="creation-mode-label">创作方式</div>
      <div className="creation-mode-tabs" role="tablist" aria-label="选择创作方式">
        {CREATION_MODES.map(([value, label], index) => <button type="button" role="tab" key={value}
          data-creation-mode={value}
          aria-selected={mode === value} tabIndex={mode === value ? 0 : -1}
          className={`subnav-tab creation-mode-tab ${mode === value ? 'active' : ''}`}
          onClick={() => switchMode(value)} onKeyDown={(event) => handleModeKey(event, index)}>{label}</button>)}
      </div>
    </div>

    <div className="creation-layout">
      <form className="creation-form" aria-busy={busy} onSubmit={(event) => {
        event.preventDefault();
        void run(async (signal) => {
          const request = creationRequest(mode, values, { persona, key });
          const job = creationReceipt(mode, await aiRequest(request.path, signal, request.payload));
          if (!signal.aborted) setDetail(request.detail + encodeURIComponent(job.taskId));
        });
      }}>
        <section className="creation-section" aria-labelledby="creation-content-heading">
          <div className="creation-section-head">
            <div><h2 id="creation-content-heading">创作内容</h2><p>{MODE_META[mode].description}</p></div>
            <span className="creation-mode-name">{CREATION_MODES.find(([value]) => value === mode)?.[1]}</span>
          </div>
          {hasPrompt(mode) && <Field label={mode === 'recap' ? '解说文案（可选）' : '创作要求'} required={promptRequired(mode)}
            hint={MODE_META[mode].promptHint || '说明你希望生成的画面或结果。'}>
            <textarea className="field creation-prompt" required={promptRequired(mode)} maxLength={2000}
              placeholder={MODE_META[mode].promptHint} value={values.prompt || ''}
              onChange={(event) => update('prompt', event.target.value)} />
            <span className="creation-character-count">{promptLength} / 2000</span>
          </Field>}
          {mode !== 'image' && <div className="creation-field-grid creation-source-grid">{sourceFields}</div>}
        </section>

        <section className="creation-section" aria-labelledby="creation-settings-heading">
          <div className="creation-section-head">
            <div><h2 id="creation-settings-heading">生成设置</h2><p>未填写的参数由所选模型决定。</p></div>
            {catalogBusy && usesModel(mode) && <span className="creation-loading" role="status"><span className="spinner" aria-hidden />读取模型</span>}
          </div>
          {catalogError && <div className="creation-inline-error" role="alert"><span>{catalogError}</span>
            <button type="button" className="btn btn-sm" onClick={() => setCatalogRevision((value) => value + 1)}>重试</button>
          </div>}
          {!catalogBusy && usesModel(mode) && !catalogError && models.length === 0 &&
            <div className="creation-empty">当前没有可用模型。请先在设置中配置模型，再回来生成。</div>}
          {settingsFields}
        </section>

        <Feedback busy={false} error={error} />
        <div className="creation-action-bar">
          <div id="creation-readiness" className="creation-readiness" role="status" aria-live="polite">
            <span className={`creation-readiness-icon ${ready ? 'ready' : ''}`} aria-hidden>{ready ? <IconCheck size={13} /> : null}</span>
            <span>{ready ? '信息已完整，可以提交' : '填写必填项后即可提交'}</span>
          </div>
          <div className="creation-actions">
            {Object.values(values).some(Boolean) && !busy && <button type="button" className="btn btn-ghost" onClick={() => setValues({})}>清空</button>}
            <button type="submit" className="btn btn-primary creation-submit"
              disabled={busy || catalogBusy || Boolean(detail)} aria-busy={busy}>
              <span className="creation-submit-content">
                {busy && <span className="creation-spinner" aria-hidden />}
                {busy ? '正在提交' : MODE_META[mode].action}
              </span>
            </button>
          </div>
        </div>
        {detail && <button type="button" className="btn creation-new-task" onClick={() => {
          setDetail('');
          setKey(crypto.randomUUID());
        }}>创建另一个任务</button>}
      </form>

      <aside className="creation-summary" aria-labelledby="creation-summary-heading">
        <h2 id="creation-summary-heading">本次任务</h2>
        <dl>
          <div><dt>方式</dt><dd>{CREATION_MODES.find(([value]) => value === mode)?.[1]}</dd></div>
          <div><dt>模型</dt><dd>{usesModel(mode) ? values.model || '尚未选择' : '由当前技能决定'}</dd></div>
          <div><dt>描述</dt><dd>{values.prompt?.trim() ? `${promptLength} 个字符` : '尚未填写'}</dd></div>
          <div><dt>参数</dt><dd>{configured.length ? configured.slice(1).join(' · ') : '使用模型默认值'}</dd></div>
        </dl>
        <p>生成后任务进度会显示在这里，刷新或切换页面前不会丢失当前模式。</p>
      </aside>
    </div>
    {detail && <JobProgress key={detail} persona={persona} path={detail} />}
  </section>;
}
