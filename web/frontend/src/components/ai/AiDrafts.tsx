import { loadAiQuery } from '../../lib/aiQuery';
import { useEffect, useState } from 'react';
import { aiPath, aiRequest, document, readPage, rows, text } from '../../lib/aiWorkspaceApi';
import type { Document, PageData } from '../../lib/aiWorkspaceApi';
import { Feedback, Pager, Result } from './AiPrimitives';
import { JobProgress } from './AiHistory';
import { AiDraftForm } from './AiDraftForm';
import { contentGroupName, jobState } from '../../lib/aiPresentation';
import { useAiRequest } from './useAiRequest';

export function AiDrafts({ persona }: { readonly persona: string }) {
  const [groups, setGroups] = useState<readonly Document[]>([]);
  const [pricing, setPricing] = useState<Document>({});
  const [page, setPage] = useState(1);
  const [data, setData] = useState<PageData>({ items: [], total: 0 });
  const [selected, setSelected] = useState<readonly string[]>([]);
  const [results, setResults] = useState<readonly Document[]>([]);
  const [detail, setDetail] = useState('');
  const [count, setCount] = useState(0);
  const [revision, setRevision] = useState(0);
  const { run, busy, error } = useAiRequest();
  const base = '/api/ai/draft-generation';
  useEffect(() => { const controller = new AbortController(); void run(async (signal) => {
    const [list, stats, prices] = await Promise.all([loadAiQuery(aiPath(base, persona, { page: String(page), pageSize: '20' }), signal, readPage), loadAiQuery(aiPath(base + '/stats', persona), signal, document), loadAiQuery(aiPath(base + '/pricing', persona), signal, document)]);
    if (list !== null && stats !== null && prices !== null && !signal.aborted) { setData(list); setCount(Number(stats.generatingCount)); setPricing(prices); }
  }, controller.signal); return () => controller.abort(); }, [persona, page, revision, run]);
  useEffect(() => { void run(async (signal) => {
    const value = await aiRequest(aiPath('/api/content/material/groups', persona, { page_size: '200' }), signal);
    if (!signal.aborted) setGroups(rows(value));
  }); }, [persona, run]);
  return <section className="ai-stack"><AiDraftForm persona={persona} pricing={pricing} groups={groups} onCreated={(ids) => { setSelected(ids); setRevision((value) => value + 1); }} />
    <Feedback busy={busy} error={error} /><h2>草稿任务</h2><p>正在生成 {count} 条</p><div className="ai-actions"><button className="btn" disabled={busy} onClick={() => setRevision((v) => v + 1)}>刷新任务</button>
      <button className="btn" disabled={busy || !selected.length || selected.length > 10} onClick={() => { void run(async (signal) => { const value = await aiRequest(aiPath(base + '/query', persona), signal, { taskIds: selected }); if (!signal.aborted) setResults(rows(value)); }); }}>查询所选任务（{selected.length}）</button></div>
    {!data.items.length && !busy && <p>还没有草稿任务。</p>}<ul className="ai-list">{data.items.map((job) => <li key={text(job.id)}><label><input type="checkbox" checked={selected.includes(text(job.id))} onChange={(e) => setSelected((current) => e.target.checked ? [...current, text(job.id)] : current.filter((id) => id !== job.id))} /> {text(document(job.request).prompt)}</label><span>{jobState(job.status)} · 分组 {contentGroupName(document(job.request).groupId, groups)}</span><button className="btn" onClick={() => setDetail(text(job.id))}>查看进度</button></li>)}</ul>
    <Pager page={page} total={data.total} onPage={setPage} />{detail && <JobProgress key={detail} persona={persona} path={base + '/' + detail} />}
    {results.map((job) => <Result key={text(job.id)} value={job} />)}
  </section>;
}
