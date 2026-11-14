import { loadAiQuery } from '../../lib/aiQuery';
import { useEffect, useState } from 'react';
import { aiPath, aiRequest, rows, text } from '../../lib/aiWorkspaceApi';
import type { Document } from '../../lib/aiWorkspaceApi';
import { Feedback, Field } from './AiPrimitives';
import { useAiRequest } from './useAiRequest';

export function AiMemory({ persona }: { readonly persona: string }) {
  const [kind, setKind] = useState('image-text');
  const [planner, setPlanner] = useState('');
  const [memory, setMemory] = useState<readonly Document[]>([]);
  const [revision, setRevision] = useState(0);
  const { run, busy, error } = useAiRequest();
  const base = '/api/ai/draft-generation/memory';
  useEffect(() => {
    const controller = new AbortController();
    void run(async (signal) => {
      const result = await loadAiQuery(aiPath(base, persona, { contentType: kind }), signal, rows);
      if (result !== null && !signal.aborted) setMemory(result);
    }, controller.signal);
    return () => controller.abort();
  }, [persona, kind, revision, run]);
  return <section className="ai-stack"><div className="ai-grid"><Field label="内容类型"><select className="field" value={kind} onChange={(e) => setKind(e.target.value)}><option value="image-text">图文</option><option value="video">视频</option></select></Field>
    <Field label="规划模型（留空使用默认）"><input className="field" value={planner} onChange={(e) => setPlanner(e.target.value)} /></Field></div>
    <Feedback busy={busy} error={error} /><div className="ai-actions"><button className="btn btn-primary" disabled={busy} onClick={() => { void run(async (signal) => { await aiRequest(aiPath(base + '/regenerate', persona), signal, { contentType: kind, plannerModel: planner }); if (!signal.aborted) setRevision((v) => v + 1); }); }}>从已有内容重新生成记忆</button><button className="btn" disabled={busy} onClick={() => setRevision((v) => v + 1)}>刷新</button></div>
    {!memory.some((entry) => rows(entry.items).length) && !busy && <p>暂无内容记忆。先生成草稿，再提取风格偏好。</p>}
    {memory.map((entry) => <section key={text(entry.id)}><p>样本数：{text(entry.sampleCount)}</p><ul className="ai-list">{rows(entry.items).map((item) => <li key={text(item.id)}><p>{text(item.text)}</p><button className="btn" disabled={busy} onClick={() => { if (window.confirm('删除这条记忆？')) void run(async (signal) => { await aiRequest(aiPath(base + '/items/' + text(item.id), persona), signal, {}, 'DELETE'); if (!signal.aborted) setRevision((v) => v + 1); }); }}>删除记忆</button></li>)}</ul></section>)}
  </section>;
}
