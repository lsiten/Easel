import { useEffect, useRef, useState } from 'react';
import { fetchOutputs } from '../../lib/api';
import type { OutputNode } from '../../lib/api';
import { channelDocument, channelError, channelJson, channelRequest, channelRows, channelText, channelWrite, readChannelApproval, readChannelRun, scopedChannelPath } from '../../lib/channelsApi';
import type { ChannelApproval, ChannelDocument, ChannelPlatform, ChannelRun, ChannelScope } from '../../lib/channelsApi';
import { ChannelMaterialPicker } from './ChannelMaterialPicker';
import { ChannelPublishOptions } from './ChannelPublishOptions';
import { ChannelReapproval } from './ChannelReapproval';
import { handoffActionUrl } from '../../lib/nativeHandoffApi';

const STATES: Readonly<Record<string, string>> = {
  prepared: '已准备', approved: '已审核', queued: '排队中', running: '处理中', submitted: '已提交，等待平台确认',
  published: '已发布', failed: '发布失败', unknown: '结果待核验', cancelled: '已取消', reconciling: '核验中',
};

export function ChannelPublish({ scope, spec }: { readonly scope: ChannelScope; readonly spec: ChannelPlatform }) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [options, setOptions] = useState('{}');
  const [files, setFiles] = useState<readonly OutputNode[]>([]);
  const [selected, setSelected] = useState<readonly string[]>([]);
  const [sourceMaterial, setSourceMaterial] = useState<ChannelDocument | null>(null);
  const [cover, setCover] = useState('');
  const [scheduled, setScheduled] = useState('');
  const [flow, setFlow] = useState('');
  const [approval, setApproval] = useState<ChannelApproval | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [run, setRun] = useState<ChannelRun | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [platformOptions, setPlatformOptions] = useState<unknown>(null);
  const [workId, setWorkId] = useState('');
  const [permalink, setPermalink] = useState('');
  const idempotency = useRef('');
  const submittedIntent = useRef('');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const active = new AbortController(); controller.current = active;
    fetchOutputs(scope.persona, active.signal).then((roots) => {
      if (active.signal.aborted) return;
      const media: OutputNode[] = [];
      function walk(node: OutputNode) {
        if (node.external || node.assets) return;
        if (node.type === 'file' && (node.kind === 'image' || node.kind === 'video')) media.push(node);
        node.children?.forEach(walk);
      }
      roots.forEach(walk); setFiles(media);
    }).catch((failure: unknown) => { if (!active.signal.aborted) setError(channelError(failure)); });
    return () => active.abort();
  }, [scope.persona]);
  function invalidate() { setApproval(null); setReviewed(false); setFlow(''); }
  function intent(): ChannelDocument {
    const references = channelRows(sourceMaterial?.media);
    return { ...scope, title, body, options: channelJson(options), cover_path: cover,
      scheduled_for: scheduled ? new Date(scheduled).toISOString() : '',
      context: sourceMaterial ? { material_id: channelText(sourceMaterial.id), material_group_id: channelText(sourceMaterial.group_id),
        media_ids: references.filter((item) => selected.includes(channelText(item.path))).map((item) => channelText(item.media_id)).filter(Boolean), source: 'platform_workbench' } : {},
      media: selected.map((path) => ({ path, kind: (files.find((file) => file.path === path)?.kind || references.find((item) => item.path === path)?.kind) === 'video' ? 'video' : 'image' })) };
  }
  async function act(name: string, operation: (signal: AbortSignal) => Promise<void>) {
    const active = controller.current;
    if (!active || active.signal.aborted) return;
    setBusy(name); setError('');
    try { await operation(active.signal); }
    catch (failure: unknown) { if (!active.signal.aborted) setError(channelError(failure)); }
    finally { if (!active.signal.aborted) setBusy(''); }
  }
  const base = `/api/channel-publish/${spec.platform}`;
  const retrying = run?.state === 'failed' && idempotency.current === run.run_id;
  return <section className="panel" aria-label="官方平台发布">
    <h2 className="panel-title">发布内容</h2>
    <p className="pv-hint">支持{spec.publish_modes.map((mode) => ({ text: '文字', image: '图片', video: '视频' })[mode] || mode).join('、')}
      {spec.limits.max_text ? ` · 正文上限 ${channelText(spec.limits.max_text)} 字符` : ''}
      {spec.limits.max_images ? ` · 最多 ${channelText(spec.limits.max_images)} 张图片` : ''}</p>
    <ChannelMaterialPicker persona={scope.persona} disabled={!!busy} onSelect={(material) => {
      setSourceMaterial(material); invalidate();
      if (material) { setTitle(channelText(material.title)); setBody(channelText(material.description)); setOptions(JSON.stringify(material.options || {}, null, 2));
        setSelected(channelRows(material.media).filter((item) => item.kind === 'image' || item.kind === 'video').map((item) => channelText(item.path))); }
    }} />
    {sourceMaterial && <p className="pv-hint">关联草稿 {channelText(sourceMaterial.title) || channelText(sourceMaterial.id)}</p>}
    <label className="field-label" htmlFor="channel-title">标题</label>
    <input id="channel-title" className="field" value={title} disabled={!!busy} onChange={(event) => { setTitle(event.target.value); invalidate(); }} />
    <label className="field-label" htmlFor="channel-body">正文</label>
    <textarea id="channel-body" className="field" rows={7} value={body} disabled={!!busy}
      onChange={(event) => { setBody(event.target.value); invalidate(); }} />
    <fieldset style={{ border: 0, padding: 0, margin: 0 }}><legend className="field-label">当前画像的媒体</legend>
      {!files.length && <p className="pv-hint">素材源中暂无图片或视频，可在内容模块上传或导入。</p>}
      <div style={{ maxHeight: '14rem', overflow: 'auto' }}>{files.map((file) => <label key={file.path} className="field-label" style={{ overflowWrap: 'anywhere' }}>
        <input type="checkbox" checked={selected.includes(file.path)} disabled={!!busy} onChange={(event) => {
          setSelected((old) => event.target.checked ? [...old, file.path] : old.filter((path) => path !== file.path)); invalidate();
        }} /> {file.name} <span className="pv-hint">{file.path}</span>
      </label>)}</div>
    </fieldset>
    {spec.cover_supported && <><label className="field-label" htmlFor="channel-cover">{spec.platform === 'tiktok' ? '图片组封面' : '内容封面'}{spec.cover_required ? '，必填' : '，可选'}</label><select id="channel-cover" className="field" value={cover} disabled={!!busy} onChange={(event) => { setCover(event.target.value); invalidate(); }}>
      <option value="">{spec.cover_required ? '选择封面图片' : '使用平台默认封面'}</option>{files.filter((file) => file.kind === 'image' && (spec.platform !== 'tiktok' || selected.includes(file.path))).map((file) => <option key={file.path} value={file.path}>{file.name}</option>)}
    </select></>}
    <label className="field-label" htmlFor="channel-scheduled">定时发布，可选，使用本机时区</label>
    <input id="channel-scheduled" className="field" type="datetime-local" value={scheduled} disabled={!!busy} onChange={(event) => { setScheduled(event.target.value); invalidate(); }} />
    <ChannelPublishOptions scope={scope} platform={spec.platform} value={options} disabled={!!busy} onChange={(value) => { setOptions(value); invalidate(); }} />
    <details><summary>平台高级选项</summary>
      {spec.option_fields.length > 0 && <p className="pv-hint" style={{ overflowWrap: 'anywhere' }}>支持的选项字段 {spec.option_fields.join(', ')}</p>}
      <details><summary>媒体格式和大小限制</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(spec.media_rules, null, 2)}</pre></details>
      <button className="btn btn-sm" disabled={!!busy} onClick={() => void act('options', async (signal) => {
        const result = await channelRequest(scopedChannelPath(spec.platform, 'publish-options', scope), { signal });
        if (!signal.aborted) setPlatformOptions(result);
      })}>读取平台可用选项</button>
      {platformOptions !== null && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(platformOptions, null, 2)}</pre>}
      <label className="field-label" htmlFor="channel-options">发布选项 JSON</label>
      <textarea id="channel-options" className="field" value={options} disabled={!!busy}
        onChange={(event) => { setOptions(event.target.value); invalidate(); }} />
    </details>
    {approval && <label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} />
      我已审核此账号的标题、正文、媒体和平台选项，确认提交发布</label>}
    <div className="subnav-tabs" style={{ flexWrap: 'wrap', marginTop: 'var(--space-3, 12px)' }}>
      <button className={`btn ${!approval ? 'btn-primary' : ''}`} disabled={!!busy || (!title.trim() && !body.trim() && !selected.length)}
        onClick={() => void act('precheck', async (signal) => {
          const draft = intent();
          const value = await channelWrite(`${base}/precheck`, draft, 'POST', signal);
          if (signal.aborted) return;
          setApproval(readChannelApproval(value)); setReviewed(false);
          idempotency.current = run?.state === 'failed' && submittedIntent.current === JSON.stringify(draft) ? run.run_id : crypto.randomUUID();
        })}>预检内容</button>
      {approval && !scheduled && <button className="btn btn-primary" disabled={!reviewed || !!busy} onClick={() => void act('publish', async (signal) => {
        const draft = intent();
        const value = await channelWrite(`${base}/publish`, { ...draft, ...approval, reviewed: true,
          idempotency_key: idempotency.current, retry: retrying }, 'POST', signal);
        if (!signal.aborted) { submittedIntent.current = JSON.stringify(draft); setRun(readChannelRun(value)); setReviewed(false); }
      })}>{retrying ? '确认重试' : '确认提交'}</button>}
      {approval && scheduled && <button className="btn btn-primary" disabled={!reviewed || !!busy || !!flow} onClick={() => void act('schedule', async (signal) => {
        const content = intent();
        const value = channelDocument(await channelWrite('/api/publish-flows', { persona: scope.persona, flow_id: idempotency.current,
          publish_at: new Date(scheduled).toISOString(), content: { title, body, options: content.options, media: content.media, cover_path: cover },
          context: content.context, items: [{ platform: spec.platform, account_uid: scope.account_uid, ...approval }], reviewed: true }, 'POST', signal));
        if (!signal.aborted) { setFlow(channelText(value.id)); setReviewed(false); }
      })}>确认定时发布</button>}
    </div>
    {busy && <p role="status">正在处理发布操作…</p>}
    {flow && <p role="status" style={{ overflowWrap: 'anywhere' }}>发布计划已创建，流程 ID {flow}。在任务记录中查看进度。</p>}
    {run && <div style={{ marginTop: 'var(--space-4, 16px)', overflowWrap: 'anywhere' }} role="status">
      <strong>{STATES[run.state] || run.state}</strong><p className="pv-hint">任务 {run.run_id}</p>
      {run.error && <p role="alert">{run.error}</p>}
      {run.cover_error && <p role="alert">封面设置失败 {run.cover_error}</p>}
      {(run.visibility || run.cover_status || run.work_link_status) && <p className="pv-hint">{[run.visibility && `可见性 ${run.visibility}`, run.work_link_status && `链接 ${run.work_link_status}`, run.cover_status && `封面 ${run.cover_status}`].filter(Boolean).join(' · ')}</p>}
      {run.permalink && <a href={run.permalink} target="_blank" rel="noreferrer">查看平台作品</a>}
      {run.requires_user_action && <p role="status">等待在平台应用中完成发布{run.action_expires_at ? `，到期时间 ${new Date(run.action_expires_at * 1000).toLocaleString()}` : ''}</p>}
      {run.requires_user_action && handoffActionUrl(run.action_url) && (!run.action_expires_at || run.action_expires_at > Date.now() / 1000)
        && <a className="btn btn-primary" href={handoffActionUrl(run.action_url)}>打开平台应用继续</a>}
      <div className="subnav-tabs" style={{ flexWrap: 'wrap' }}>
        <button className="btn btn-sm" disabled={!!busy} onClick={() => { setRun(null); submittedIntent.current = ''; idempotency.current = ''; invalidate(); }}>新建发布</button>
        <button className="btn btn-sm" disabled={!!busy} onClick={() => void act('status', async (signal) => {
          const value = await channelRequest(`${base}/${run.run_id}?${new URLSearchParams(scope)}`, { signal });
          if (!signal.aborted) setRun(readChannelRun(value));
        })}>刷新任务状态</button>
        {['submitted', 'running'].includes(run.state) && <button className="btn btn-sm" disabled={!!busy} onClick={() => void act('finalize', async (signal) => {
          const value = await channelWrite(`${base}/${run.run_id}/finalize`, scope, 'POST', signal);
          if (!signal.aborted) setRun(readChannelRun(value));
        })}>继续平台确认</button>}
      </div>
      {run.state === 'submitted' && <ChannelReapproval key={run.run_id} platform={spec.platform} runId={run.run_id} scope={scope} onChanged={(value) => setRun(readChannelRun(value))} />}
      {['unknown', 'submitted'].includes(run.state) && <details><summary>核验平台作品</summary>
        <label className="field-label" htmlFor="channel-work-id">平台作品 ID</label><input id="channel-work-id" className="field" value={workId} onChange={(event) => setWorkId(event.target.value)} />
        <label className="field-label" htmlFor="channel-permalink">平台作品链接</label><input id="channel-permalink" className="field" value={permalink} onChange={(event) => setPermalink(event.target.value)} />
        <button className="btn" disabled={!!busy || (!workId && !permalink)} onClick={() => void act('reconcile', async (signal) => {
          const value = await channelWrite(`${base}/${run.run_id}/reconcile`, { ...scope, platform_work_id: workId, permalink }, 'POST', signal);
          if (!signal.aborted) setRun(readChannelRun(value));
        })}>读取平台证据并核验</button></details>}
    </div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
