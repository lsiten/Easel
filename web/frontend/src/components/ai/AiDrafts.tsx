import { loadAiQuery } from '../../lib/aiQuery';
import { useEffect, useState } from 'react';
import { aiPath, aiRequest, document, readPage, rows, text } from '../../lib/aiWorkspaceApi';
import type { Document, PageData } from '../../lib/aiWorkspaceApi';
import { Pager, Result } from './AiPrimitives';
import { JobProgress } from './AiHistory';
import { AiDraftForm } from './AiDraftForm';
import { contentGroupName, jobState } from '../../lib/aiPresentation';
import { useAiRequest } from './useAiRequest';
import { IconLayers, IconRefresh } from '../icons';

function draftTitle(job: Document): string {
  const request = document(job.request);
  const prompt = text(request.prompt).trim();
  return prompt ? prompt.split('\n')[0].slice(0, 90) : '未命名草稿';
}

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

  useEffect(() => {
    const controller = new AbortController();
    void run(async (signal) => {
      const [list, stats, prices] = await Promise.all([
        loadAiQuery(aiPath(base, persona, { page: String(page), pageSize: '20' }), signal, readPage),
        loadAiQuery(aiPath(base + '/stats', persona), signal, document),
        loadAiQuery(aiPath(base + '/pricing', persona), signal, document),
      ]);
      if (list !== null && stats !== null && prices !== null && !signal.aborted) {
        setData(list);
        setCount(Number(stats.generatingCount));
        setPricing(prices);
      }
    }, controller.signal);
    return () => controller.abort();
  }, [persona, page, revision, run]);

  useEffect(() => {
    void run(async (signal) => {
      const value = await aiRequest(aiPath('/api/content/material/groups', persona, { page_size: '200' }), signal);
      if (!signal.aborted) setGroups(rows(value));
    });
  }, [persona, run]);

  function toggle(id: string, checked: boolean) {
    setSelected((current) => checked ? [...current, id] : current.filter((value) => value !== id));
  }

  return <section className="ai-workspace ai-stack creation-workspace draft-page">
    <header className="creation-page-head">
      <div>
        <span className="creation-page-eyebrow"><IconLayers size={14} /> 批量工作流</span>
        <h1>批量生成</h1>
        <p>用一条创作方向批量规划图文或视频草稿，并在下方查看生成进度。</p>
      </div>
      <div className="creation-page-meta">
        <span>正在生成</span>
        <strong>{count}</strong>
      </div>
    </header>

    <AiDraftForm persona={persona} pricing={pricing} groups={groups} onCreated={(ids) => {
      setSelected(ids);
      setRevision((value) => value + 1);
    }} />

    <section className="creation-section draft-jobs" aria-labelledby="draft-jobs-heading">
      <div className="creation-section-head">
        <div><h2 id="draft-jobs-heading">草稿任务</h2><p>选择不超过 10 个任务查看结果，或打开单个任务查看实时进度。</p></div>
        <div className="creation-actions">
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setRevision((value) => value + 1)}>
            <IconRefresh size={14} /> 刷新任务
          </button>
          <button type="button" className="btn btn-sm" disabled={busy || !selected.length || selected.length > 10} onClick={() => {
            void run(async (signal) => {
              const value = await aiRequest(aiPath(base + '/query', persona), signal, { taskIds: selected });
              if (!signal.aborted) setResults(rows(value));
            });
          }}>查询所选任务（{selected.length}）</button>
        </div>
      </div>

      {error && <div className="video-inline-error" role="alert">{error}</div>}
      {busy && !data.items.length ? <div className="draft-list-skeleton" aria-label="正在加载草稿任务">
        {[0, 1, 2].map((item) => <div key={item} aria-hidden />)}
      </div>
        : !data.items.length ? <div className="creation-empty-state draft-empty">
          <div><h3><IconLayers size={18} /> 还没有草稿任务</h3><p>完成上方设置后创建第一条批量任务。</p></div>
        </div>
          : <ul className="draft-job-list">
            {data.items.map((job) => {
              const id = text(job.id);
              const request = document(job.request);
              return <li key={id}>
                <label className="draft-job-check">
                  <input type="checkbox" checked={selected.includes(id)} onChange={(event) => toggle(id, event.target.checked)} />
                  <span><strong>{draftTitle(job)}</strong><small>{jobState(job.status)} · 分组 {contentGroupName(request.groupId, groups)}</small></span>
                </label>
                <button type="button" className="btn btn-sm" onClick={() => setDetail(id)}>查看进度</button>
              </li>;
            })}
          </ul>}

      <Pager page={page} total={data.total} onPage={setPage} />
      {detail && <JobProgress key={detail} persona={persona} path={base + '/' + detail} />}
      {results.length > 0 && <div className="draft-results">
        <h3>任务结果</h3>
        {results.map((job) => <Result key={text(job.id)} value={job} />)}
      </div>}
    </section>
  </section>;
}
