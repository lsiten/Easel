import { GENERATION_TYPES, jobState } from '../../lib/aiPresentation';
import { loadAiQuery } from '../../lib/aiQuery';
import { useEffect, useState } from 'react';
import { aiPath, aiRequest, document, readPage, text } from '../../lib/aiWorkspaceApi';
import type { Document, PageData } from '../../lib/aiWorkspaceApi';
import { Asset, Feedback, Field, Pager, Result } from './AiPrimitives';
import { useAiRequest } from './useAiRequest';

export function AiHistory({ persona, assets = false }: { readonly persona: string; readonly assets?: boolean }) {
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [model, setModel] = useState('');
  const [channel, setChannel] = useState('');
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<PageData>({ items: [], total: 0 });
  const { run, busy, error } = useAiRequest();
  useEffect(() => { const controller = new AbortController(); void run(async (signal) => {
    const value = await loadAiQuery(aiPath(`/api/ai/${assets ? 'assets' : 'logs'}`, persona,
      { page: String(page), page_size: '20', ...(!assets ? { type: kind, status, model, channel } : {}) }), signal, readPage);
    if (value !== null && !signal.aborted) setData(value);
  }, controller.signal); return () => controller.abort(); }, [persona, assets, page, kind, status, model, channel, revision, run]);
  return <section className="ai-stack">
    {!assets && <div className="ai-grid"><Field label="类型"><select className="field" value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }}>{GENERATION_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
      <Field label="状态"><select className="field" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>{['', 'generating', 'success', 'failed'].map((value) => <option key={value} value={value}>{value ? jobState(value) : '全部'}</option>)}</select></Field>
      <Field label="模型"><input className="field" value={model} onChange={(e) => { setModel(e.target.value); setPage(1); }} /></Field>
      <Field label="提供商"><input className="field" value={channel} onChange={(e) => { setChannel(e.target.value); setPage(1); }} /></Field></div>}
    <button className="btn" disabled={busy} onClick={() => setRevision((v) => v + 1)}>刷新</button><Feedback busy={busy} error={error} />
    {!data.items.length && !busy && !error && <p>{assets ? '还没有生成素材。完成图片或视频生成后会出现在这里。' : '没有匹配的记录。可调整筛选或开始对话、生成内容。'}</p>}
    {!assets && <h2>生成记录</h2>}
    <div className={assets ? 'ai-grid' : 'ai-stack'}>{data.items.map((item, index) => assets ? <Asset key={text(item.id || item.path) || index} asset={item} />
      : <article className="ai-row" key={text(item.id)}><h3>{text(item.model || item.request_model || item.kind || item.type) || '任务'}</h3><p>{text(item.channel)} · {jobState(item.state || item.status)}</p><Result value={item} /></article>)}</div>
    <Pager page={page} total={data.total} onPage={setPage} />
  </section>;
}
export function JobProgress({ persona, path, onResult }: { readonly persona: string; readonly path: string; readonly onResult?: (job: Document) => void }) {
  const { run, busy, error } = useAiRequest();
  const [job, setJob] = useState<Document | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => { void run(async (signal) => { const value = document(await aiRequest(aiPath(path, persona), signal)); if (!signal.aborted) { setJob(value); onResult?.(value); } }); }, [path, persona, revision, run, onResult]);
  useEffect(() => {
    if (job && ['succeeded', 'completed', 'failed', 'unknown'].includes(text(job.state || job.status))) return;
    const timer = window.setTimeout(() => setRevision((v) => v + 1), 3000); return () => window.clearTimeout(timer);
  }, [job, revision]);
  return <div><Feedback busy={busy} error={error} />{job && <Result value={job} />}<button className="btn" disabled={busy} onClick={() => setRevision((v) => v + 1)}>刷新进度</button></div>;
}
