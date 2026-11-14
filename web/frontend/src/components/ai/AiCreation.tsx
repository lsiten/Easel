import { useEffect, useState } from 'react';
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

export function AiCreation({ persona }: { readonly persona: string }) {
  const [mode, setMode] = useState<CreationMode>('image');
  const [values, setValues] = useState<CreationValues>({});
  const [models, setModels] = useState<readonly Document[]>([]);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [detail, setDetail] = useState('');
  const [key, setKey] = useState(() => crypto.randomUUID());
  const { run, busy, error } = useAiRequest();
  const catalog = mode === 'video' ? 'video/generation' : `image/${mode === 'edit' ? 'edit' : 'generation'}`;
  useEffect(() => {
    setModels([]); setCatalogError('');
    if (!['image', 'edit', 'video'].includes(mode)) { setCatalogBusy(false); return; }
    const controller = new AbortController();
    setCatalogBusy(true);
    void loadAiQuery(aiPath('/api/ai/models/' + catalog, persona), controller.signal, rows).then((result) => {
      if (result !== null && !controller.signal.aborted) setModels(result);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setCatalogError(channelError(failure));
    }).finally(() => { if (!controller.signal.aborted) setCatalogBusy(false); });
    return () => controller.abort();
  }, [persona, catalog, mode]);
  function field(name: string, label: string, required = false, type = 'text') {
    return <Field key={name} label={label}><input className="field" required={required} type={type} value={values[name] || ''} onChange={(e) => setValues((old) => ({ ...old, [name]: e.target.value }))} /></Field>;
  }
  function area(name: string, label: string, required = false) {
    return <Field key={name} label={label}><textarea className="field" required={required} value={values[name] || ''} onChange={(e) => setValues((old) => ({ ...old, [name]: e.target.value }))} /></Field>;
  }
  const selected = models.find((model) => model.name === values.model);
  function option(name: string, label: string, keys: readonly string[], numeric = false) {
    return <ModelOption label={label} choices={modelChoices(selected, ...keys)} value={values[name] || ''} numeric={numeric}
      onChange={(value) => setValues((old) => ({ ...old, [name]: value }))} />;
  }
  return <section className="ai-stack"><div className="subnav-tabs">{CREATION_MODES.map(([value, label]) => <button className={`subnav-tab ${mode === value ? 'active' : ''}`} aria-pressed={mode === value} key={value} onClick={() => { if (value === mode) return; setModels([]); setMode(value); setValues({}); setDetail(''); setKey(crypto.randomUUID()); }}>{label}</button>)}</div>
    <form className="ai-stack" onSubmit={(e) => { e.preventDefault(); void run(async (signal) => {
      const request = creationRequest(mode, values, { persona, key });
      const job = creationReceipt(mode, await aiRequest(request.path, signal, request.payload));
      if (!signal.aborted) setDetail(request.detail + encodeURIComponent(job.taskId));
    }); }}>
      <div className="ai-grid">{!['style', 'direct-edit'].includes(mode) && area('prompt', mode === 'recap' ? '解说文案（可选）' : '创作要求', !['recap', 'aideo'].includes(mode))}
        {['image', 'edit', 'video'].includes(mode) && <Field label="模型"><select className="field" required disabled={catalogBusy} value={values.model || ''} onChange={(e) => { const model = models.find((item) => item.name === e.target.value); setValues((old) => ({ ...old, model: e.target.value, channel: text(model?.channel), ratio: '', resolution: '', imageSize: '', size: '', quality: '', style: '', duration: '', mode: '' })); }}><option value="">选择已配置模型</option>{models.map((model) => <option key={text(model.name)} value={text(model.name)}>{text(model.name)} · {text(model.channel)}</option>)}</select></Field>}
        {selected && <p>{text(selected.description)} {selected.configured === false ? '提供商尚未配置，请前往配置。' : ''}</p>}
        {['image', 'edit'].includes(mode) && <>{option('size', '图片尺寸', ['sizes'])}{option('ratio', '宽高比', ['supportedAspectRatios', 'aspectRatios'])}
          {values.channel === 'gemini' && option('imageSize', 'Gemini 原生分辨率', ['resolutions'])}
          {mode === 'image' ? <>{field('quantity', '张数（1–10）', false, 'number')}{option('quality', '画质', ['qualities'])}{option('style', '风格', ['styles'])}</> : <>{area('images', '参考图片地址（每行一个）', true)}{field('mask', '遮罩图片地址')}</>}</>}
        {mode === 'video' && <>{option('mode', '生成模式', ['modes'])}{option('duration', '时长（秒）', ['durations'], true)}{option('resolution', '分辨率', ['resolutions'])}{option('ratio', '宽高比', ['aspectRatios', 'supportedAspectRatios'])}{field('groupId', '内容分组 ID')}{area('images', '参考图片地址（每行一个）')}{field('imageTail', '尾帧图片地址')}{area('videos', '参考视频地址（每行一个）')}{area('audios', '参考音频地址（每行一个）')}</>}
        {mode === 'aideo' && <>{area('inputs', '视频输入地址或 VID（每行一个）', true)}{field('space', '视频空间')}<Field label="技能"><select className="field" value={values.skill || ''} onChange={(e) => setValues((old) => ({ ...old, skill: e.target.value }))}>{['', 'AITranslation', 'Erase', 'Highlight', 'VCreative', 'Vision'].map((v) => <option key={v} value={v}>{v || '按创作要求执行'}</option>)}</select></Field><details><summary>高级技能参数</summary>{area('params', '技能参数 JSON')}</details></>}
        {mode === 'style' && <>{field('video', '视频输入地址', true)}{field('style', '目标风格')}{field('resolution', '分辨率（480p / 720p / 1080p）')}</>}
        {mode === 'recap' && <>{area('inputs', '视频 VID（每行一个）', true)}{field('style', '解说风格')}{field('speed', '语速（0.5–2）', false, 'number')}{field('pause', '停顿毫秒', false, 'number')}{field('voice', '声音类型')}{field('appId', '语音应用 ID')}{field('cluster', '语音集群')}</>}
        {mode === 'direct-edit' && <>{field('video', '视频来源（地址或 VID）', !values.track)}{field('start', '开始秒', false, 'number')}{field('end', '结束秒', false, 'number')}{field('width', '画布宽度', false, 'number')}{field('height', '画布高度', false, 'number')}{field('space', '输出空间')}<details><summary>高级轨道编辑</summary>{area('track', 'Track JSON（覆盖上方单轨道设置）')}</details></>}
        {mode === 'character' && <>{field('video', '视频地址（或填写任务 ID）', !values.taskId)}{field('taskId', '已有视频任务 ID')}{field('start', '片段开始秒', false, 'number')}{field('end', '片段结束秒（最长 3 秒）', false, 'number')}</>}
        {mode === 'remix' && field('taskId', '原视频任务 ID', true)}
      </div><Feedback busy={busy || catalogBusy} error={error || catalogError} /><div className="ai-actions"><button className="btn btn-primary" disabled={busy || catalogBusy || !!detail}>提交生成</button>{detail && <button type="button" className="btn" onClick={() => { setDetail(''); setKey(crypto.randomUUID()); }}>创建另一个任务</button>}</div>
    </form>{detail && <JobProgress key={detail} persona={persona} path={detail} />}
  </section>;
}
