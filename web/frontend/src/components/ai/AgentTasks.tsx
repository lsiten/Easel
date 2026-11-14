import { jobState, publicShareError } from '../../lib/aiPresentation';
import { useEffect, useRef, useState } from 'react';
import { loadAiQuery } from '../../lib/aiQuery';
import { aiPath, aiRequest, agentSubmit, document, readPage, rows, text } from '../../lib/aiWorkspaceApi';
import type { Document, PageData } from '../../lib/aiWorkspaceApi';
import { useAiRequest } from './useAiRequest';
import { Feedback, Field, Pager } from './AiPrimitives';
import { AgentTaskDetail } from './AgentTaskDetail';

export function AgentTasks({ persona }: { readonly persona: string }) {
  const detailRequest = useRef<AbortController | null>(null);
  const detailId = useRef('');
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState('');
  const [favorite, setFavorite] = useState(false);
  const [data, setData] = useState<PageData>({ items: [], total: 0 });
  const [selected, setSelected] = useState<Document | null>(null);
  const [prompt, setPrompt] = useState('');
  const [revision, setRevision] = useState(0);
  const { run, busy, error } = useAiRequest();
  const base = '/api/ai/agent/tasks';
  useEffect(() => {
    const controller = new AbortController();
    void run(async (signal) => {
      const value = await loadAiQuery(aiPath(base, persona, { page: String(page), pageSize: '20', keyword, favoriteOnly: String(favorite) }), signal, readPage);
      if (value !== null && !signal.aborted) setData(value);
    }, controller.signal);
    return () => controller.abort();
  }, [page, persona, keyword, favorite, revision, run]);
  useEffect(() => {
    if (!selected || selected.status !== 'running') return;
    const controller = new AbortController();
    const selectionRequest = detailRequest.current;
    let polling = false;
    const timer = window.setInterval(() => { if (polling) return; polling = true; void run(async (signal) => {
      const next = await loadAiQuery(aiPath(`${base}/${text(selected.id)}`, persona), signal, document);
      if (next !== null && !signal.aborted && detailId.current === text(selected.id) && detailRequest.current === selectionRequest) setSelected(next);
    }, controller.signal).finally(() => { polling = false; }); }, 1500);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [selected, persona, run]);
  function close() {
    detailRequest.current?.abort(); detailId.current = ''; setSelected(null);
  }
  function open(id: string) {
    detailRequest.current?.abort();
    const controller = new AbortController(); detailRequest.current = controller; detailId.current = id;
    setSelected((current) => current?.id === id ? current : null);
    void run(async (signal) => {
      const value = await loadAiQuery(aiPath(`${base}/${id}`, persona), signal, document);
      if (value !== null && !signal.aborted && detailId.current === id) setSelected(value);
    }, controller.signal);
  }
  function submit() {
    void run(async (signal) => {
      const id = await agentSubmit(aiPath(base, persona), signal, { prompt, ...(selected ? { taskId: text(selected.id) } : {}) });
      if (!signal.aborted) { setPrompt(''); open(id); setRevision((value) => value + 1); }
    });
  }
  return <section className="ai-stack">
    <div className="ai-grid"><Field label="搜索任务"><input className="field" value={keyword} onChange={(e) => { setKeyword(e.target.value); setPage(1); }} /></Field>
      <label><input type="checkbox" checked={favorite} onChange={(e) => { setFavorite(e.target.checked); setPage(1); }} /> 只看收藏</label></div>
    <Feedback busy={busy} error={error} />
    <div className="ai-actions"><button className="btn" onClick={() => setRevision((v) => v + 1)}>刷新任务</button><button className="btn" onClick={close}>新任务</button></div>
    {!data.items.length && !busy && <p>暂无任务。已有画像对话会自动出现在这里，也可以创建新任务。</p>}
    <ul className="ai-list">{data.items.map((task) => <li key={text(task.id)}><button className="btn" onClick={() => open(text(task.id))}>{text(task.title)}</button><span>{jobState(task.status)}{task.favoritedAt ? ' · 已收藏' : ''}</span></li>)}</ul>
    <Pager page={page} total={data.total} onPage={setPage} />
    {selected && <AgentTaskDetail key={text(selected.id)} persona={persona} task={selected} onChanged={() => { open(text(selected.id)); setRevision((v) => v + 1); }} onDeleted={() => { close(); setRevision((v) => v + 1); }} />}
    <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="ai-stack">
      <Field label={selected ? '继续此任务' : '新任务内容'}><textarea className="field" required maxLength={4000} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></Field>
      <button className="btn btn-primary" disabled={busy || selected?.status === 'running'}>{selected ? '继续对话' : '创建任务'}</button>
    </form>
  </section>;
}
export function PublicAgentTask({ token }: { readonly token: string }) {
  const { run, busy, error } = useAiRequest();
  const [task, setTask] = useState<Document | null>(null);
  useEffect(() => { void run(async (signal) => { const value = document(await aiRequest(`/api/ai/agent/tasks/shared/${encodeURIComponent(token)}`, signal)); if (!signal.aborted) setTask(value); }); }, [token, run]);
  return <main className="accounts-page ai-workspace"><h1 className="page-title">{task ? text(task.title) : '分享的对话'}</h1><Feedback busy={busy} error={publicShareError(error)} />
    {task && rows(task.messages).map((message, index) => <article key={index}><strong>{message.type === 'user' ? '用户' : '助手'}</strong><p className="ai-prose">{text(message.text)}</p></article>)}</main>;
}
