import { jobState } from '../../lib/aiPresentation';
import { useState } from 'react';
import { aiPath, aiRequest, document, rows, text } from '../../lib/aiWorkspaceApi';
import type { Document } from '../../lib/aiWorkspaceApi';
import { Field, Feedback } from './AiPrimitives';
import { useAiRequest } from './useAiRequest';

function activeQuestionId(task: Document): string {
  if (task.status !== 'running') return '';
  let turnPrefix = '';
  let active = '';
  for (const event of rows(task.messages)) {
    const id = text(event.uuid);
    if (event.type === 'user') { turnPrefix = id.endsWith(':user') ? id.slice(0, -4) : ''; active = ''; }
    else if (turnPrefix && id.startsWith(turnPrefix)) {
      if (event.type === 'question') active = id;
      else if (['token', 'assistant', 'error', 'done'].includes(text(event.type))) active = '';
    }
  }
  return active;
}

export function AgentTaskDetail({ persona, task, onChanged, onDeleted }: {
  readonly persona: string; readonly task: Document; readonly onChanged: () => void; readonly onDeleted: () => void;
}) {
  const [title, setTitle] = useState(text(task.title));
  const [rating, setRating] = useState(text(task.rating) || '5');
  const [comment, setComment] = useState(text(task.ratingComment));
  const [ttl, setTtl] = useState('604800');
  const [share, setShare] = useState('');
  const [expiry, setExpiry] = useState('');
  const { run, busy, error } = useAiRequest();
  const base = `/api/ai/agent/tasks/${text(task.id)}`;
  function action(suffix: string, payload: unknown = {}, method = 'POST') {
    void run(async (signal) => { await aiRequest(aiPath(base + suffix, persona), signal, payload, method); if (!signal.aborted) onChanged(); });
  }
  const activeQuestion = activeQuestionId(task);
  const messages = rows(task.messages).filter((event) => ['user', 'assistant', 'question', 'error', 'activity'].includes(text(event.type)));
  return <section className="ai-stack panel" aria-label="任务详情">
    <h2>{text(task.title)}</h2><p>{jobState(task.status)}</p><Feedback busy={busy} error={error} />
    <div className="ai-actions"><button className="btn" disabled={busy} onClick={() => action('/favorite', {}, task.favoritedAt ? 'DELETE' : 'POST')}>{task.favoritedAt ? '取消收藏' : '收藏'}</button>
      <button className="btn" disabled={busy || task.status !== 'running'} onClick={() => action('/abort')}>停止任务</button>
      <button className="btn btn-danger" disabled={busy} onClick={() => { if (window.confirm('删除任务及关联对话？分享链接也会失效。')) void run(async (signal) => { await aiRequest(aiPath(base, persona), signal, {}, 'DELETE'); if (!signal.aborted) onDeleted(); }); }}>删除任务</button></div>
    <form className="ai-actions" onSubmit={(e) => { e.preventDefault(); action('', { title }, 'PATCH'); }}><Field label="任务名称"><input className="field" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} /></Field><button className="btn" disabled={busy}>保存名称</button></form>
    <div className="ai-messages">{messages.map((event) => event.type === 'question'
      ? <TaskQuestion key={text(event.uuid)} event={event} active={text(event.uuid) === activeQuestion} persona={persona} taskId={text(task.id)} onChanged={onChanged} />
      : <article key={text(event.uuid)}><strong>{event.type === 'user' ? '用户' : event.type === 'assistant' ? '助手' : event.type === 'activity' ? '进度' : '错误'}</strong><p className="ai-prose">{typeof event.data === 'string' ? event.data : rows(event.data).map((part) => text(part.text)).join('\n')}</p></article>)}</div>
    <form className="ai-grid" onSubmit={(e) => { e.preventDefault(); action('/rating', { rating: Number(rating), comment }); }}>
      <Field label="评分"><select className="field" value={rating} onChange={(e) => setRating(e.target.value)}>{[1, 2, 3, 4, 5].map((value) => <option key={value}>{value}</option>)}</select></Field>
      <Field label="评价"><input className="field" maxLength={1000} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
      <button className="btn" disabled={busy || !['completed', 'requires_action'].includes(text(task.status))}>保存评价</button></form>
    <div className="ai-actions"><Field label="分享有效期"><select className="field" value={ttl} onChange={(e) => setTtl(e.target.value)}><option value="3600">1 小时</option><option value="86400">1 天</option><option value="604800">7 天</option></select></Field>
      <button className="btn" disabled={busy} onClick={() => { void run(async (signal) => { const value = document(await aiRequest(aiPath(base + '/share', persona), signal, { ttlSeconds: Number(ttl) })); if (!signal.aborted) { setShare(`${location.origin}${location.pathname}?aiShare=${encodeURIComponent(text(value.token))}`); setExpiry(new Date(Number(value.expiresAt) * 1000).toLocaleString()); } }); }}>生成分享链接</button></div>
    {share && <p><a href={share} target="_blank" rel="noreferrer">打开公开分享</a> · 到期 {expiry}<input className="field" aria-label="分享链接" readOnly value={share} onFocus={(e) => e.target.select()} /></p>}
  </section>;
}
function TaskQuestion({ event, active, persona, taskId, onChanged }: { readonly event: Document; readonly active: boolean; readonly persona: string; readonly taskId: string; readonly onChanged: () => void }) {
  const [answers, setAnswers] = useState<Readonly<Record<string, string>>>({});
  const [answered, setAnswered] = useState(false);
  const { run, busy, error } = useAiRequest();
  let question: Document;
  try { question = typeof event.data === 'string' ? document(JSON.parse(event.data)) : document(event.data); }
  catch { return <p>问题格式不可用，请返回原对话回答。</p>; }
  const questions = rows(question.questions);
  if (!active || answered) return <section className="ai-stack" aria-label="历史问题">
    {questions.map((item) => <div key={text(item.questionId)}><strong>{text(item.question)}</strong>
      {answers[text(item.questionId)] && <p>{answers[text(item.questionId)]}</p>}</div>)}
    <p>{answered ? '已回答' : '历史问题（只读）'}</p>
  </section>;
  return <form className="ai-stack" onSubmit={(e) => { e.preventDefault(); void run(async (signal) => {
    const response = document(await aiRequest(aiPath(`/api/ai/agent/tasks/${taskId}/questions/${text(question.id)}`, persona), signal,
      { answers: Object.fromEntries(questions.map((item) => [text(item.questionId), [answers[text(item.questionId)] || '']])) }));
    if (response.ok !== true) throw new Error(text(response.error) || '回答未被接受');
    if (!signal.aborted) { setAnswered(true); onChanged(); }
  }); }}>
    {questions.map((item) => <Field key={text(item.questionId)} label={text(item.question)}><input className="field" required disabled={answered} value={answers[text(item.questionId)] || ''} list={`options-${text(item.questionId)}`}
      onChange={(e) => setAnswers((current) => ({ ...current, [text(item.questionId)]: e.target.value }))} /><datalist id={`options-${text(item.questionId)}`}>{rows(item.options).map((option) => <option key={text(option.value || option.label)} value={text(option.value || option.label)}>{text(option.label)}</option>)}</datalist></Field>)}
    <Feedback busy={busy} error={error} /><button className="btn" disabled={busy || answered}>{answered ? '已回答' : '提交回答'}</button></form>;
}
