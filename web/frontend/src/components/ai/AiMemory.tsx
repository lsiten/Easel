import { loadAiQuery } from '../../lib/aiQuery';
import { useEffect, useState } from 'react';
import { aiPath, aiRequest, rows, text } from '../../lib/aiWorkspaceApi';
import type { Document } from '../../lib/aiWorkspaceApi';
import { Feedback, Field } from './AiPrimitives';
import { useAiRequest } from './useAiRequest';
import { IconRefresh, IconText, IconTrash } from '../icons';

export function AiMemory({ persona }: { readonly persona: string }) {
  const [kind, setKind] = useState('image-text');
  const [planner, setPlanner] = useState('');
  const [planners, setPlanners] = useState<readonly Document[]>([]);
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

  useEffect(() => {
    const controller = new AbortController();
    void run(async (signal) => {
      const result = await loadAiQuery(aiPath('/api/ai/models/chat', persona), signal, rows);
      if (result !== null && !signal.aborted) setPlanners(result.filter((model) => model.configured !== false));
    }, controller.signal);
    return () => controller.abort();
  }, [persona, run]);

  function regenerate() {
    void run(async (signal) => {
      await aiRequest(aiPath(base + '/regenerate', persona), signal, { contentType: kind, plannerModel: planner });
      if (!signal.aborted) setRevision((value) => value + 1);
    });
  }

  const itemCount = memory.reduce((total, entry) => total + rows(entry.items).length, 0);
  const empty = !memory.some((entry) => rows(entry.items).length);

  return <section className="ai-workspace ai-stack creation-workspace memory-page">
    <header className="creation-page-head">
      <div>
        <span className="creation-page-eyebrow"><IconText size={14} /> 内容偏好</span>
        <h1>内容记忆</h1>
        <p>从已有草稿中提取稳定的表达偏好，供后续批量生成复用。</p>
      </div>
      <div className="creation-page-meta">
        <span>已提取</span>
        <strong>{itemCount}</strong>
      </div>
    </header>

    <form className="creation-form" onSubmit={(event) => { event.preventDefault(); regenerate(); }}>
      <section className="creation-section" aria-labelledby="memory-settings-heading">
        <div className="creation-section-head">
          <div><h2 id="memory-settings-heading">提取设置</h2><p>选择内容类型，必要时指定规划模型。</p></div>
        </div>
        <div className="creation-field-grid">
          <Field label="内容类型">
            <select className="field" value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="image-text">图文</option>
              <option value="video">视频</option>
            </select>
          </Field>
          <Field label="规划模型（对话通道）" hint="留空时使用对话通道默认模型。">
            <select className="field" value={planner} onChange={(event) => setPlanner(event.target.value)}>
              <option value="">跟随对话通道默认模型</option>
              {planners.map((model) => <option key={text(model.name)} value={text(model.name)}>{text(model.name)}</option>)}
            </select>
          </Field>
        </div>
        <div className="memory-actions">
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setRevision((value) => value + 1)}>
            <IconRefresh size={14} /> 刷新
          </button>
          <button type="submit" className="btn btn-primary creation-submit" disabled={busy} aria-busy={busy}>
            <span className="creation-submit-content">{busy && <span className="creation-spinner" aria-hidden />}{busy ? '正在提取' : '从已有内容重新提取'}</span>
          </button>
        </div>
        <Feedback busy={false} error={error} />
      </section>

      <section className="creation-section" aria-labelledby="memory-list-heading" aria-busy={busy}>
        <div className="creation-section-head">
          <div><h2 id="memory-list-heading">已提取的记忆</h2><p>每条记忆都来自已生成内容，删除后不再用于后续创作。</p></div>
          {!busy && <span className="creation-mode-name">{itemCount} 条</span>}
        </div>
        {busy && empty ? <div className="draft-list-skeleton" aria-label="正在加载内容记忆">
          {[0, 1].map((item) => <div key={item} aria-hidden />)}
        </div>
          : empty ? <div className="creation-empty-state memory-empty">
            <div><h3><IconText size={18} /> 还没有内容记忆</h3><p>先生成几条草稿，再从已有内容提取风格和结构偏好。</p></div>
          </div>
            : <div className="memory-groups">
              {memory.map((entry) => {
                const items = rows(entry.items);
                if (!items.length) return null;
                return <article key={text(entry.id)} className="memory-group">
                  <header><h3>内容记忆</h3><span>{text(entry.sampleCount)} 个样本</span></header>
                  <ul>
                    {items.map((item) => <li key={text(item.id)}>
                      <p>{text(item.text)}</p>
                      <button type="button" className="memory-delete" disabled={busy} aria-label={`删除记忆：${text(item.text).slice(0, 30)}`} onClick={() => {
                        if (!window.confirm('删除这条记忆？')) return;
                        void run(async (signal) => {
                          await aiRequest(aiPath(base + '/items/' + text(item.id), persona), signal, {}, 'DELETE');
                          if (!signal.aborted) setRevision((value) => value + 1);
                        });
                      }}><IconTrash size={13} /> 删除</button>
                    </li>)}
                  </ul>
                </article>;
              })}
            </div>}
      </section>
    </form>
  </section>;
}
