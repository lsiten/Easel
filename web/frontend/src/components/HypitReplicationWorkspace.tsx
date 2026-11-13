import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  approveHypitVariant,
  cancelHypitRun,
  createHypitVariant,
  fetchHypitArtifacts,
  fetchHypitLogs,
  fetchHypitPricing,
  fetchHypitReadiness,
  fetchHypitReports,
  fetchHypitRun,
  fetchHypitVariants,
  importHypitArtifact,
  planHypitRun,
  rerunHypitRun,
  startHypitRun,
  type HypitPricing,
  type HypitRun,
  type HypitScope,
  type HypitVariant,
} from '../lib/api';
import { IconCheck, IconRefresh, IconRetry, IconVideo } from './icons';

interface Props {
  session?: string;
  persona?: string;
}

type LoadState<T> = { status: 'idle' | 'loading' | 'ready' | 'error'; data: T | null; error: string };
type UnknownRecord = Record<string, unknown>;

const emptyState = <T,>(): LoadState<T> => ({ status: 'idle', data: null, error: '' });
const asRecord = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
const stringList = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const normalizeVariants = (value: unknown): { variants: HypitVariant[] } => {
  const record = asRecord(value);
  return { variants: Array.isArray(record.variants) ? record.variants.filter(item => item && typeof item === 'object') as HypitVariant[] : [] };
};

function safeText(value: unknown, fallback = '—'): string {
  if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') return fallback;
  const text = String(value)
    .replace(/(?:bearer\s+|api[_ -]?key\s*[=:]\s*|token\s*[=:]\s*|password\s*[=:]\s*)[^\s,;]+/gi, '[已隐藏]')
    .replace(/(?:\/Users\/|\/home\/|[A-Za-z]:\\|\/tmp\/)[^\s,;]+/g, '[路径已隐藏]');
  return text.length > 180 ? `${text.slice(0, 177)}…` : text;
}

function safeData(value: unknown): unknown {
  if (typeof value === 'string') return safeText(value);
  if (Array.isArray(value)) return value.map(safeData);
  if (value && typeof value === 'object') {
    const result: UnknownRecord = {};
    for (const [key, item] of Object.entries(value)) {
      const lower = key.toLowerCase();
      if (lower.includes('secret') || lower.includes('password') || lower.includes('token') || lower.includes('credential') || lower.endsWith('root') || lower === 'path') continue;
      result[key] = safeData(item);
    }
    return result;
  }
  return value;
}

function describe(value: unknown): string {
  const clean = safeData(value);
  if (clean === undefined || clean === null || clean === '') return '暂无数据';
  if (typeof clean === 'object') {
    try { return JSON.stringify(clean, null, 2); } catch { return '数据不可读'; }
  }
  return String(clean);
}

function stateLabel(state: unknown): string {
  const value = safeText(state, 'unknown').toLowerCase();
  return ({ planned: '已规划', running: '构建中', built: '已生成', succeeded: '完成', failed: '失败', cancelled: '已取消' } as Record<string, string>)[value] || '未知状态';
}

function motionLabel(value: unknown): string {
  const text = safeText(value, 'unknown').toLowerCase();
  if (text === 'static' || text === 'still') return '静态';
  if (text === 'slow') return '慢速';
  if (text === 'fast' || text === 'dynamic') return '快速';
  return '未知';
}

function Card({ title, eyebrow, children, className = '' }: { title: string; eyebrow?: string; children: ReactNode; className?: string }) {
  return <section className={`hypit-card ${className}`}>
    <div className="hypit-card-head">
      <div>{eyebrow && <div className="hypit-eyebrow">{eyebrow}</div>}<h2>{title}</h2></div>
    </div>
    {children}
  </section>;
}

function InlineError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="hypit-inline-error" role="alert"><span>{message || '加载失败'}</span><button className="btn btn-sm" onClick={onRetry}><IconRetry size={13} /> 重试</button></div>;
}

function DetailList({ value }: { value: unknown }) {
  const record = asRecord(value);
  const entries = Object.entries(record);
  if (!entries.length) return <div className="hypit-muted">暂无记录</div>;
  return <dl className="hypit-detail-list">{entries.map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{safeText(typeof item === 'object' ? describe(item) : item)}</dd></div>)}</dl>;
}

export default function HypitReplicationWorkspace({ session, persona }: Props) {
  const scope = useMemo<HypitScope | null>(() => session && persona ? { session_id: session, profile_id: persona } : null, [session, persona]);
  const [readiness, setReadiness] = useState<LoadState<{ ready: boolean; engine: string }>>(emptyState());
  const [samplePricing, setSamplePricing] = useState<LoadState<HypitPricing>>(emptyState());
  const [fullPricing, setFullPricing] = useState<LoadState<HypitPricing>>(emptyState());
  const [variants, setVariants] = useState<LoadState<{ variants: HypitVariant[] }>>(emptyState());
  const [selectedVariant, setSelectedVariant] = useState('');
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [variantDraft, setVariantDraft] = useState('');
  const [run, setRun] = useState<LoadState<HypitRun>>(emptyState());
  const [logs, setLogs] = useState<string[]>([]);
  const [reports, setReports] = useState<unknown[]>([]);
  const [artifacts, setArtifacts] = useState<Array<{ name: string; kind: string }>>([]);
  const [runError, setRunError] = useState('');
  const [buildKind, setBuildKind] = useState<'sample' | 'full'>('sample');
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');
  const generation = useRef(0);

  const load = useCallback(async () => {
    if (!scope) return;
    const current = ++generation.current;
    setReadiness({ status: 'loading', data: null, error: '' });
    setSamplePricing({ status: 'loading', data: null, error: '' });
    setFullPricing({ status: 'loading', data: null, error: '' });
    setVariants({ status: 'loading', data: null, error: '' });
    setRun(emptyState()); setLogs([]); setReports([]); setArtifacts([]); setNotice('');
    const results = await Promise.allSettled([
      fetchHypitReadiness(scope), fetchHypitPricing(scope, 'sample'), fetchHypitPricing(scope, 'full'), fetchHypitVariants(scope),
    ]);
    if (current !== generation.current) return;
    const apply = <T,>(result: PromiseSettledResult<T>, setter: (state: LoadState<T>) => void) => {
      if (result.status === 'fulfilled') setter({ status: 'ready', data: result.value, error: '' });
      else setter({ status: 'error', data: null, error: result.reason instanceof Error ? result.reason.message : '请求失败' });
    };
    apply(results[0], setReadiness);
    apply(results[1], setSamplePricing);
    apply(results[2], setFullPricing);
    if (results[3].status === 'fulfilled') setVariants({ status: 'ready', data: normalizeVariants(results[3].value), error: '' });
    else setVariants({ status: 'error', data: null, error: results[3].reason instanceof Error ? results[3].reason.message : '请求失败' });
  }, [scope]);

  useEffect(() => { void load(); return () => { generation.current += 1; }; }, [load]);
  useEffect(() => {
    const list = variants.data?.variants || [];
    if (!list.some(item => item.variant_id === selectedVariant)) setSelectedVariant(list[0]?.variant_id || '');
    setCompareIds(previous => previous.filter(id => list.some(item => item.variant_id === id)).slice(0, 2));
  }, [variants.data, selectedVariant]);

  const selected = variants.data?.variants.find(item => item.variant_id === selectedVariant) || null;
  const approvalState = safeText(selected?.approval_state, 'pending').toLowerCase();
  const canBuild = !!scope && readiness.data?.ready === true && (buildKind === 'sample' || approvalState === 'approved');
  const pricing = buildKind === 'full' ? fullPricing.data : samplePricing.data;

  const refreshRun = useCallback(async (runId: string, currentScope: HypitScope) => {
    const requestGeneration = generation.current;
    try {
      const status = await fetchHypitRun(runId, currentScope);
      if (requestGeneration !== generation.current) return;
      setRun({ status: 'ready', data: status, error: '' });
      const [logResult, reportResult, artifactResult] = await Promise.allSettled([
        fetchHypitLogs(runId, currentScope), fetchHypitReports(runId, currentScope), fetchHypitArtifacts(runId, currentScope),
      ]);
      if (requestGeneration !== generation.current) return;
      if (logResult.status === 'fulfilled') setLogs(Array.isArray(logResult.value.logs) ? logResult.value.logs.map(item => safeText(item)) : []);
      if (reportResult.status === 'fulfilled') setReports(Array.isArray(reportResult.value.reports) ? reportResult.value.reports : []);
      if (artifactResult.status === 'fulfilled') setArtifacts(Array.isArray(artifactResult.value.artifacts) ? artifactResult.value.artifacts : []);
    } catch (error) {
      if (requestGeneration !== generation.current) return;
      setRun({ status: 'error', data: null, error: error instanceof Error ? error.message : '运行状态加载失败' });
    }
  }, []);

  useEffect(() => {
    const runId = run.data?.id || run.data?.run_id;
    if (!runId || !scope || !['planned', 'running', 'built'].includes(String(run.data?.state))) return;
    const timer = window.setTimeout(() => void refreshRun(String(runId), scope), 1800);
    return () => window.clearTimeout(timer);
  }, [run.data?.id, run.data?.run_id, run.data?.state, refreshRun, scope]);

  const build = async () => {
    if (!scope || !pricing || !canBuild) return;
    setBusy('build'); setRunError(''); setNotice('');
    try {
      const fingerprint = selected && typeof selected.sample_fingerprint === 'string' ? selected.sample_fingerprint : undefined;
      const planned = await planHypitRun(scope, { kind: buildKind, pricing, input_fingerprint: fingerprint });
      setRun({ status: 'ready', data: planned, error: '' });
      const runId = planned.id || planned.run_id;
      if (runId) {
        try { await startHypitRun(String(runId), scope); } catch { /* A planned run remains inspectable when the worker is unavailable. */ }
        await refreshRun(String(runId), scope);
      }
    } catch (error) { setRunError(error instanceof Error ? error.message : '无法创建 Build'); }
    finally { setBusy(''); }
  };

  const retry = async () => {
    if (!scope || !run.data) return;
    const runId = run.data.id || run.data.run_id;
    if (!runId) return;
    setBusy('retry');
    try { const result = await rerunHypitRun(String(runId), scope); setRun({ status: 'ready', data: result, error: '' }); await refreshRun(String(runId), scope); }
    catch (error) { setRunError(error instanceof Error ? error.message : '重试失败'); }
    finally { setBusy(''); }
  };

  const cancel = async () => {
    if (!scope || !run.data) return;
    const runId = run.data.id || run.data.run_id;
    if (!runId) return;
    setBusy('cancel');
    try { const result = await cancelHypitRun(String(runId), scope); setRun({ status: 'ready', data: result, error: '' }); await refreshRun(String(runId), scope); }
    catch (error) { setRunError(error instanceof Error ? error.message : '取消失败'); }
    finally { setBusy(''); }
  };

  const createVariant = async () => {
    if (!scope) return;
    setBusy('variant'); setNotice('');
    try {
      const created = await createHypitVariant(scope, {
        variant_id: variantDraft.trim() || undefined,
        brief: { concept: '待补充的复制方向' }, treatment: { style: 'unknown' },
        analysis: { motion: 'unknown' }, timeline: { scenes: [] },
        selected_anchors: [], selected_assets: [], reference_evidence: [],
      });
      setVariants(previous => ({ status: 'ready', data: { variants: [...(previous.data?.variants || []), created] }, error: '' }));
      setSelectedVariant(created.variant_id); setVariantDraft(''); setNotice(`已创建变体 ${created.variant_id}`);
    } catch (error) { setNotice(error instanceof Error ? error.message : '变体创建失败'); }
    finally { setBusy(''); }
  };

  const approve = async () => {
    if (!scope || !selected) return;
    setBusy('approve'); setNotice('');
    try {
      const approved = await approveHypitVariant(selected.variant_id, scope);
      setVariants(previous => ({ status: 'ready', data: { variants: (previous.data?.variants || []).map(item => item.variant_id === approved.variant_id ? approved : item) }, error: '' }));
      setNotice('样例已批准，完整 Build 已解锁。');
    } catch (error) { setNotice(error instanceof Error ? error.message : '样例批准失败'); }
    finally { setBusy(''); }
  };

  const importArtifact = async (artifact: { name: string; kind: string }) => {
    if (!scope || !run.data) return;
    const runId = run.data.id || run.data.run_id;
    if (!runId) return;
    setBusy(`import:${artifact.name}`); setNotice('');
    try { await importHypitArtifact(String(runId), scope, artifact.name); setNotice(`已导入 ${safeText(artifact.name)} 到内容库。`); }
    catch (error) { setNotice(error instanceof Error ? error.message : '产物导入失败'); }
    finally { setBusy(''); }
  };

  if (!scope) return <section className="hypit-workspace hypit-empty-scope" aria-label="Hypit 复制工作台"><IconVideo size={18} /><div><strong>Hypit 复制工作台</strong><p>请选择账号并打开一个会话后开始分析。</p></div></section>;

  return <section className="hypit-workspace" aria-label="Hypit 复制工作台">
    <div className="hypit-workspace-head">
      <div><div className="hypit-eyebrow">独立复制引擎 · {persona}</div><h1><IconVideo size={20} /> Hypit 复制工作台</h1><p>参考证据、镜头时间线与构建产物在 Hypit 命名空间中管理。</p></div>
      <button className="btn btn-sm" onClick={() => void load()}><IconRefresh size={14} /> 刷新</button>
    </div>
    <div className="hypit-boundary" role="note"><strong>Hypit 来源边界</strong><span>Hypit 参考与产物独立保存。OpenFilm Studio 仍是另一套制作与预览来源。</span></div>
    {notice && <div className="hypit-notice" role="status">{notice}</div>}
    <div className="hypit-grid">
      <Card title="准备度与费用" eyebrow="Build gate">
        {readiness.status === 'loading' ? <div className="hypit-loading">正在检查 Hypit…</div> : readiness.status === 'error' ? <InlineError message={readiness.error} onRetry={() => void load()} /> : <div className="hypit-readiness"><span className={`hypit-status-dot ${readiness.data?.ready ? 'ok' : 'off'}`} />{readiness.data?.ready ? 'Hypit 就绪' : 'Hypit 不可用'}<span className="hypit-source-tag">engine: {safeText(readiness.data?.engine, 'hypit')}</span></div>}
        <div className="hypit-build-toggle" role="group" aria-label="Build 类型"><button className={buildKind === 'sample' ? 'active' : ''} onClick={() => setBuildKind('sample')}>Sample · 1 单位</button><button className={buildKind === 'full' ? 'active' : ''} onClick={() => setBuildKind('full')}>Full · 4 单位</button></div>
        {pricing ? <div className="hypit-price"><span>预计费用</span><strong>{safeText(pricing.estimated_cost)} units</strong><small>收据 {safeText(pricing.pricing_id)}</small></div> : <div className="hypit-muted">费用收据加载后才能 Build。</div>}
        {buildKind === 'full' && approvalState !== 'approved' && <div className="hypit-gate">请先批准所选变体的 Sample，Full Build 才可用。</div>}
        {runError && <div className="hypit-error" role="alert">{runError}</div>}
        <button className="btn btn-sm hypit-primary-action" disabled={!canBuild || busy === 'build'} onClick={() => void build()}>{busy === 'build' ? '准备中…' : `开始 ${buildKind === 'sample' ? 'Sample' : 'Full'} Build`}</button>
      </Card>

      <Card title="变体与样例批准" eyebrow="Branch selection">
        {variants.status === 'loading' ? <div className="hypit-loading">正在加载变体…</div> : variants.status === 'error' ? <InlineError message={variants.error} onRetry={() => void load()} /> : <>
          <div className="hypit-variant-list">{(variants.data?.variants || []).map(item => <label key={item.variant_id} className={`hypit-variant ${selectedVariant === item.variant_id ? 'selected' : ''}`}><input type="radio" name="hypit-variant" checked={selectedVariant === item.variant_id} onChange={() => setSelectedVariant(item.variant_id)} /><span><strong>{safeText(item.variant_id)}</strong><small>{safeText(item.approval_state, 'pending')} · {item.parent_variant_id ? `基于 ${safeText(item.parent_variant_id)}` : '根变体'}</small></span><button type="button" className={`hypit-compare ${compareIds.includes(item.variant_id) ? 'selected' : ''}`} onClick={() => setCompareIds(ids => ids.includes(item.variant_id) ? ids.filter(id => id !== item.variant_id) : ids.length < 2 ? [...ids, item.variant_id] : [ids[1], item.variant_id])}>{compareIds.includes(item.variant_id) ? '已对比' : '对比'}</button></label>)}{!(variants.data?.variants || []).length && <div className="hypit-muted">暂无变体。创建一个复制方向开始。</div>}</div>
          <div className="hypit-variant-create"><input value={variantDraft} onChange={event => setVariantDraft(event.target.value)} placeholder="可选变体 ID" aria-label="变体 ID" /><button className="btn btn-sm" disabled={busy === 'variant'} onClick={() => void createVariant()}>{busy === 'variant' ? '创建中…' : '新建变体'}</button></div>
          {selected && <div className="hypit-approval"><span>当前状态 <b>{safeText(selected.approval_state, 'pending')}</b></span><button className="btn btn-sm" disabled={approvalState === 'approved' || busy === 'approve'} onClick={() => void approve()}>{approvalState === 'approved' ? <><IconCheck size={13} /> 已批准</> : busy === 'approve' ? '处理中…' : '批准 Sample'}</button></div>}
        </>}
      </Card>

      <Card title="参考证据" eyebrow="Evidence" className="hypit-span-2">
        {selected ? <div className="hypit-evidence-grid"><div><h3>Anchors</h3>{stringList(selected.selected_anchors).length ? <ul>{stringList(selected.selected_anchors).map(value => <li key={value}>{safeText(value)}</li>)}</ul> : <div className="hypit-muted">暂无选定锚点</div>}</div><div><h3>Assets</h3>{stringList(selected.selected_assets).length ? <ul>{stringList(selected.selected_assets).map(value => <li key={value}>{safeText(value)}</li>)}</ul> : <div className="hypit-muted">暂无素材引用</div>}</div><div><h3>Evidence records</h3>{Array.isArray(selected.reference_evidence) && selected.reference_evidence.length ? <ul>{selected.reference_evidence.map((value, index) => <li key={index}>{safeText(asRecord(value).source_id || asRecord(value).kind || `evidence-${index + 1}`)}</li>)}</ul> : <div className="hypit-muted">暂无参考证据</div>}</div></div> : <div className="hypit-muted">请选择一个变体查看证据。</div>}
      </Card>

      <Card title="分析与镜头时间线" eyebrow="Analysis / Timeline">
        {selected ? <div className="hypit-analysis-layout"><div><h3>Motion</h3><div className="hypit-motion-pill">{motionLabel(asRecord(selected.analysis).motion)}</div><DetailList value={selected.analysis} /></div><div><h3>Scenes</h3>{(() => { const timeline = asRecord(selected.timeline); const scenes = Array.isArray(timeline.scenes) ? timeline.scenes : Array.isArray(timeline.shots) ? timeline.shots : []; return scenes.length ? <ol className="hypit-scenes">{scenes.map((scene, index) => { const item = asRecord(scene); return <li key={index}><strong>{safeText(item.title || item.name || `镜头 ${index + 1}`)}</strong><span>{safeText(item.start ?? item.in ?? '—')} → {safeText(item.end ?? item.out ?? '—')}</span><em>{motionLabel(item.motion || item.motion_state)}</em></li>; })}</ol> : <div className="hypit-muted">暂无镜头时间线</div>; })()}</div></div> : <div className="hypit-muted">请选择一个变体查看分析。</div>}
      </Card>

      <Card title="Build 进度与日志" eyebrow="Run monitor">
        {!run.data ? <div className="hypit-muted">尚未开始 Build。</div> : <><div className="hypit-run-head"><span className={`hypit-run-state state-${safeText(run.data.state, 'unknown')}`}>{stateLabel(run.data.state)}</span><span>Run {safeText(run.data.id || run.data.run_id)}</span></div>{run.data.error && <div className="hypit-error">{safeText(run.data.error)}</div>}<div className="hypit-log-box" aria-label="Build 日志">{logs.length ? logs.map((line, index) => <div key={`${index}-${line}`}>{line}</div>) : <span className="hypit-muted">等待日志…</span>}</div><div className="hypit-run-actions">{['planned', 'running', 'built'].includes(String(run.data.state)) && <button className="btn btn-sm" disabled={busy === 'cancel'} onClick={() => void cancel()}>取消</button>}{['failed', 'cancelled'].includes(String(run.data.state)) && <button className="btn btn-sm" disabled={busy === 'retry'} onClick={() => void retry()}><IconRetry size={13} /> 重试</button>}<button className="btn btn-sm" onClick={() => { const runId = run.data?.id || run.data?.run_id; if (runId && scope) void refreshRun(String(runId), scope); }}><IconRefresh size={13} /> 刷新状态</button></div></>}
      </Card>

      <Card title="报告与产物" eyebrow="Outputs">
        {reports.length ? <div className="hypit-reports">{reports.map((report, index) => <pre key={index}>{describe(report)}</pre>)}</div> : <div className="hypit-muted">Build 完成后显示报告。</div>}
        <div className="hypit-artifacts">{artifacts.length ? artifacts.map(artifact => <div key={`${artifact.kind}-${artifact.name}`} className="hypit-artifact"><span><strong>{safeText(artifact.name)}</strong><small>{safeText(artifact.kind)}</small></span><button className="btn btn-sm" disabled={busy === `import:${artifact.name}`} onClick={() => void importArtifact(artifact)}>导入到内容库</button></div>) : <div className="hypit-muted">暂无可导入产物。</div>}</div>
      </Card>
    </div>
    {compareIds.length > 0 && <div className="hypit-compare-bar" role="status">正在对比 {compareIds.map(id => safeText(id)).join(' 与 ')}<button className="btn btn-sm" onClick={() => setCompareIds([])}>清除</button></div>}
  </section>;
}

