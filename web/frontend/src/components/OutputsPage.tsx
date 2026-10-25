import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import type { CSSProperties } from 'react';
import { fetchOutputs, fetchOutputContent, fetchOutputChildren, fetchKbScope, mediaUrl, deleteOutput, kbQuery, kbSave, kbIngest, kbStatus } from '../lib/api';
import type { OutputNode, OutputMeta, KbDoc, KbQueryResult, KbDepositResult } from '../lib/api';
import { renderMarkdown } from '../lib/sanitize';
import { IconOutputs, IconImage, IconVideo, IconMusic, IconFile, IconFolder, IconRefresh, IconChevron, IconTrash, IconSearch, IconBookmark } from './icons';

const FILTERS: { key: string; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'image', label: '图片' },
  { key: 'video', label: '视频' },
  { key: 'audio', label: '音频' },
  { key: 'text', label: '文档' },
];

const KIND_LABEL: Record<string, string> = {
  article: '文章', 'xhs-note': '小红书', video: '视频', cards: '卡片',
  poster: '海报', audio: '音频', other: '其他',
};
const STATUS_LABEL: Record<string, string> = { draft: '草稿', ready: '待发', published: '已发' };
const STATUS_COLOR: Record<string, string> = { draft: 'var(--text-tertiary)', ready: 'var(--amber)', published: 'var(--green)' };

const badge: CSSProperties = {
  fontSize: 11, padding: '1px 7px', borderRadius: 999,
  background: 'var(--surface-hover)', color: 'var(--text-secondary)', whiteSpace: 'nowrap',
};
const statusBadge = (s: string): CSSProperties => ({
  ...badge,
  background: `color-mix(in srgb, ${STATUS_COLOR[s] || 'var(--text-tertiary)'} 13%, transparent)`,
  color: STATUS_COLOR[s] || 'var(--text-secondary)',
});
const labelStyle: CSSProperties = { fontSize: 12.5, fontWeight: 600, color: 'var(--text-secondary)' };

function kindIcon(kind: string | undefined, size = 30) {
  if (kind === 'video') return <IconVideo size={size} />;
  if (kind === 'audio') return <IconMusic size={size} />;
  if (kind === 'image') return <IconImage size={size} />;
  return <IconFile size={size} />;
}
const isHtml = (name: string) => /\.html?$/i.test(name);
const kindLabel = (f: OutputNode) =>
  f.kind === 'text' ? (isHtml(f.name) ? '卡片' : '文档')
    : f.kind === 'image' ? '图片' : f.kind === 'video' ? '视频' : f.kind === 'audio' ? '音频' : '文件';

/** 递归找目录下第一张图/视频作封面缩略图。 */
function firstMedia(node: OutputNode): OutputNode | null {
  if (node.type === 'file') return (node.kind === 'image' || node.kind === 'video') ? node : null;
  for (const c of node.children || []) {
    const m = firstMedia(c);
    if (m) return m;
  }
  return null;
}

/** 展示头声明的封面 → 伪 file 节点（供 Thumb 渲染）。 */
function coverNode(m?: OutputMeta): OutputNode | null {
  if (!m?.cover) return null;
  const kind = /\.(mp4|mov|webm|mkv)$/i.test(m.cover) ? 'video' : 'image';
  return { name: 'cover', type: 'file', path: m.cover, kind } as OutputNode;
}


function Thumb({ f, big }: { f: OutputNode | null; big?: boolean }) {
  if (f && f.kind === 'image') return <img src={mediaUrl(f.path)} alt="" loading="lazy" />;
  if (f && f.kind === 'video') return <video src={mediaUrl(f.path)} preload="metadata" muted />;
  return <div className="gcard-ph">{kindIcon(f?.kind, big ? 34 : 30)}</div>;
}

interface OutputsPageProps {
  /** 从对话跳转进来的目录（outputs 相对路径，不含 outputs/ 前缀）；消费后由父层清空 */
  jumpPath?: string;
  onJumpHandled?: () => void;
  /** 当前画像：知识库(外部内容源)按其聚焦范围过滤，查询结果随画像而不同；空=通用全量 */
  persona?: string;
}

export default function OutputsPage({ jumpPath, onJumpHandled, persona }: OutputsPageProps) {
  const [roots, setRoots] = useState<OutputNode[]>([]);
  const [treeError, setTreeError] = useState('');
  const [stack, setStack] = useState<string[]>([]);   // 当前所在的文件夹名称路径
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState<OutputNode | null>(null);
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const reqSeq = useRef(0);
  const [lazyMap, setLazyMap] = useState<Record<string, OutputNode[]>>({});
  const [extLoading, setExtLoading] = useState(false);

  const [kbScope, setKbScope] = useState<{ focused: boolean; patterns: string[] }>({ focused: false, patterns: [] });

  // 知识库检索 / 沉淀（集成项目 Otter Wiki / llm-wiki，随画像聚焦）
  const [kbReady, setKbReady] = useState(false);
  const [kbQ, setKbQ] = useState('');
  const [kbResults, setKbResults] = useState<KbQueryResult | null>(null);
  const [kbSearching, setKbSearching] = useState(false);
  const [kbError, setKbError] = useState('');
  // 沉淀弹窗：create-only 事务，先 dry-run 预览，确认才落库（同名冲突 fail-closed）
  const [depositOpen, setDepositOpen] = useState(false);
  const [depositTab, setDepositTab] = useState<'ingest' | 'save'>('ingest');
  const [ingestForm, setIngestForm] = useState({ source: '', target: '', title: '' });
  const [saveForm, setSaveForm] = useState({ title: '', content: '', target: '' });
  const [depositResult, setDepositResult] = useState<KbDepositResult | null>(null);
  const [depositBusy, setDepositBusy] = useState(false);

  const load = useCallback(() => {
    setTreeError('');
    fetchOutputs(persona).then(setRoots).catch(() => setTreeError('加载产物列表失败'));
  }, [persona]);
  // 切换画像：回到顶层、清空懒加载缓存、重新拉取聚焦后的内容树。
  useEffect(() => { setStack([]); setLazyMap({}); }, [persona]);
  useEffect(() => { load(); }, [load]);
  // 当前知识库查询视角（有聚焦时显示徽标）。
  useEffect(() => {
    fetchKbScope(persona).then(setKbScope).catch(() => setKbScope({ focused: false, patterns: [] }));
  }, [persona]);
  // 知识库是否就绪（集成项目 llm-wiki）：控制检索 / 沉淀入口可用性。
  useEffect(() => {
    kbStatus(persona).then((s) => setKbReady(s.available)).catch(() => setKbReady(false));
  }, [persona]);

  // 当前层节点：外部内容源（本地知识库）深层无内联子节点，按路径懒加载。
  const { currentNodes, extDirToLoad } = useMemo(() => {
    let nodes: OutputNode[] = roots;
    let pending: OutputNode | null = null;
    for (const nm of stack) {
      const found = nodes.find((n) => n.type === 'dir' && n.name === nm);
      if (!found) { pending = null; break; }
      if (found.children && found.children.length > 0) {
        nodes = found.children;
        pending = null;
      } else {
        const cached = lazyMap[found.path];
        if (cached) {
          nodes = cached;
          pending = null;
        } else {
          pending = found;
          nodes = [];
          break;
        }
      }
    }
    return { currentNodes: nodes, extDirToLoad: pending };
  }, [roots, stack, lazyMap]);
  // 外部内容源深层目录：按当前层路径懒加载子节点（知识库可能上万文件，不全量入树）。
  const extDirPath = extDirToLoad?.path;
  useEffect(() => {
    if (!extDirPath) return;
    let cancelled = false;
    setExtLoading(true);
    fetchOutputChildren(extDirPath, persona)
      .then((children) => { if (!cancelled) setLazyMap((m) => ({ ...m, [extDirPath!]: children })); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setExtLoading(false); });
    return () => { cancelled = true; };
  }, [extDirPath, persona]);

  const dirs = useMemo(
    () => currentNodes.filter((n) => n.type === 'dir').sort((a, b) => (b.mtime || 0) - (a.mtime || 0)),
    [currentNodes]);
  const files = useMemo(() => {
    const fs = currentNodes.filter((n) => n.type === 'file').sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
    return filter === 'all' ? fs : fs.filter((f) => f.kind === filter);
  }, [currentNodes, filter]);

  const atTop = stack.length === 0;
  const atProjectRoot = stack.length === 1;
  // 当前项目的展示头（进入项目后才有），用于「成品/素材」分区
  const projectMeta = useMemo(
    () => (stack.length >= 1 ? roots.find((r) => r.name === stack[0])?.meta : undefined),
    [roots, stack]);
  const deliverableSet = useMemo(
    () => new Set(atProjectRoot ? (projectMeta?.deliverablePaths || []) : []),
    [projectMeta, atProjectRoot]);
  const hasSplit = atProjectRoot && deliverableSet.size > 0;
  const deliverableFiles = useMemo(
    () => (hasSplit ? files.filter((f) => deliverableSet.has(f.path)) : []),
    [files, deliverableSet, hasSplit]);
  const restFiles = useMemo(
    () => (hasSplit ? files.filter((f) => !deliverableSet.has(f.path)) : files),
    [files, deliverableSet, hasSplit]);

  const enterDir = useCallback((name: string) => { setStack((s) => [...s, name]); setFilter('all'); }, []);
  const goTo = useCallback((depth: number) => { setStack((s) => s.slice(0, depth)); setFilter('all'); }, []);

  const remove = useCallback(async (node: OutputNode, e: React.MouseEvent) => {
    e.stopPropagation();
    if (node.external) return;   // 外部知识源只读：入口已隐藏删除，这里再兜一层
    const isDir = node.type === 'dir';
    const label = isDir ? `项目/文件夹「${node.meta?.title || node.name}」及其全部内容` : `文件「${node.name}」`;
    if (!window.confirm(`确定删除${label}？\n此操作不可恢复。`)) return;
    try {
      await deleteOutput(node.path);
      setSelected((cur) => (cur?.path === node.path ? null : cur));
      load();
    } catch (err) {
      alert((err as Error).message || '删除失败');
    }
  }, [load]);

  const open = useCallback(async (f: OutputNode) => {
    const seq = ++reqSeq.current;
    setSelected(f); setContent('');
    if (f.kind === 'text' && !isHtml(f.name)) {
      setLoading(true);
      try {
        const res = await fetchOutputContent(f.path);
        if (seq === reqSeq.current) setContent(res.isBinary ? '' : res.content);
      } finally { if (seq === reqSeq.current) setLoading(false); }
    }
  }, []);

  // 知识库检索：query 随画像聚焦（通用 = 全量）；结果按 relative_path 命中。
  const runKbQuery = useCallback(async () => {
    const q = kbQ.trim();
    if (!q) return;
    setKbSearching(true); setKbError('');
    try {
      const res = await kbQuery(q, 10, persona);
      setKbResults(res);
      if (!res.available) setKbError(res.error || '知识库未就绪');
    } catch (e) {
      setKbError((e as Error).message || '查询失败');
    } finally { setKbSearching(false); }
  }, [kbQ, persona]);

  // 点开检索结果 → 复用 open() 打开原文：@<source>/<rel> 经 _resolve_external 解析渲染。
  const openKbDoc = useCallback((doc: KbDoc, source: string) => {
    const rel = doc.relative_path;
    const name = rel.split('/').pop() || rel;
    const path = source ? `@${source}/${rel}` : rel;
    open({ name, type: 'file', path, kind: 'text', external: true } as OutputNode);
  }, [open]);

  // 沉淀：apply=false 仅 dry-run 预览（不落库）；apply=true 才写库（同名冲突 fail-closed，绝不覆盖）。
  const runDeposit = useCallback(async (apply: boolean) => {
    setDepositBusy(true); setDepositResult(null);
    try {
      let res: KbDepositResult;
      if (depositTab === 'ingest') {
        res = await kbIngest(
          { source: ingestForm.source.trim(), target: ingestForm.target.trim() || undefined, title: ingestForm.title.trim() || undefined, apply },
          persona,
        );
      } else {
        res = await kbSave(
          { title: saveForm.title.trim(), content: saveForm.content, target: saveForm.target.trim() || undefined, apply },
          persona,
        );
      }
      setDepositResult(res);
    } catch (e) {
      setDepositResult({ applied: false, error: (e as Error).message || '沉淀失败' });
    } finally { setDepositBusy(false); }
  }, [depositTab, ingestForm, saveForm, persona]);

  // 等内容树加载后区分文件与目录；文件链接直接打开预览。
  useEffect(() => {
    if (!jumpPath || roots.length === 0) return;
    const parts = jumpPath.split('/').filter(Boolean);
    const parent: string[] = [];
    let nodes = roots;
    let node: OutputNode | undefined;
    for (const part of parts) {
      node = nodes.find(candidate => candidate.name === part);
      if (!node || node.type !== 'dir') break;
      parent.push(part);
      nodes = node.children || [];
    }
    if (node?.path !== parts.join('/')) node = undefined;
    setStack(parent);
    setFilter('all');
    if (node?.type === 'file') void open(node);
    else setSelected(null);
    onJumpHandled?.();
  }, [jumpPath, roots, open, onJumpHandled]);

  const preview = () => {
    if (!selected) return null;
    const url = mediaUrl(selected.path);
    if (selected.kind === 'image') return <img src={url} alt={selected.name} style={{ maxWidth: '100%', borderRadius: 'var(--radius)' }} />;
    if (selected.kind === 'video') return <video src={url} controls style={{ maxWidth: '100%', borderRadius: 'var(--radius)' }} />;
    if (selected.kind === 'audio') return <audio src={url} controls style={{ width: '100%' }} />;
    if (selected.kind === 'text' && isHtml(selected.name)) return (
      <>
        {/* allow-scripts：让预览页自带的「复制到公众号」按钮(execCommand('copy'))能运行；
            allow="clipboard-write"：授予剪贴板写权限。不给 allow-same-origin —— iframe 保持
            opaque origin，脚本跑得起来但访问不到本站，安全。(修复：内嵌预览里复制按钮点了没反应) */}
        <iframe src={`${url}?v=${selected.mtime ?? 0}`} title={selected.name} sandbox="allow-scripts" allow="clipboard-write"
          style={{ width: '100%', height: '68vh', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: '#fff' }} />
        <div style={{ marginTop: 8 }}><a href={url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-start)', fontSize: 13 }}>在新标签打开 ↗</a></div>
      </>
    );
    if (selected.kind === 'text') {
      if (loading) return <div className="loading"><div className="spinner" />加载中…</div>;
      return <div className="outputs-viewer-content" dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }} />;
    }
    return <div style={{ color: 'var(--text-secondary)', fontSize: 14 }}>无法预览。<a href={url} download style={{ color: 'var(--accent-start)' }}>下载 {selected.name}</a></div>;
  };

  /** 项目/文件夹卡片：顶层项目用展示头（标题/平台/状态/封面），嵌套子文件夹回退朴素样式。 */
  const renderDir = (d: OutputNode) => {
    const m = d.meta;
    const cover = coverNode(m) || firstMedia(d);
    return (
      <div key={d.path} className="card card-hover gcard" onClick={() => enterDir(d.name)}>
        <div className="gcard-thumb">
          <span className="gcard-kind">{m?.kind ? (KIND_LABEL[m.kind] || m.kind) : '文件夹'}</span>
          {!d.external && <button className="gcard-del" title="删除" onClick={(e) => remove(d, e)}><IconTrash size={14} /></button>}
          {cover ? <Thumb f={cover} big /> : <div className="gcard-ph"><IconFolder size={38} /></div>}
        </div>
        <div className="gcard-meta">
          <div className="gcard-name" title={m?.title || d.name}>
            {!m && <IconFolder size={13} />} {m?.title || d.name}
          </div>
          <div className="gcard-sub" style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            {d.external && <span style={badge}>知识库</span>}
            {m?.platform && <span style={badge}>{m.platform}</span>}
            {m?.status && <span style={statusBadge(m.status)}>{STATUS_LABEL[m.status] || m.status}</span>}
            <span>{d.fileCount ?? 0} 个文件</span>
          </div>
        </div>
      </div>
    );
  };

  const renderFile = (f: OutputNode) => (
    <div key={f.path} className="card card-hover gcard" onClick={() => open(f)}>
      <div className="gcard-thumb">
        <span className="gcard-kind">{kindLabel(f)}</span>
        {!f.external && <button className="gcard-del" title="删除" onClick={(e) => remove(f, e)}><IconTrash size={14} /></button>}
        <Thumb f={f} />
      </div>
      <div className="gcard-meta">
        <div className="gcard-name" title={f.name}>{f.name}</div>
        {f.external && <div className="gcard-sub" style={{ display: 'flex', gap: 6 }}><span style={badge}>知识库</span></div>}
      </div>
    </div>
  );

  const empty = dirs.length === 0 && files.length === 0;

  return (
    <div className="gallery-page">
      <div className="gallery-head">
        <div>
          <h1 className="page-title">
            <IconOutputs size={21} />
            <span className="crumb" onClick={() => goTo(0)}>内容库</span>
            {stack.map((name, i) => (
              <span key={i}>
                <span className="crumb-sep">/</span>
                {i === stack.length - 1
                  ? (projectMeta?.title && i === 0 ? projectMeta.title : name)
                  : <span className="crumb" onClick={() => goTo(i + 1)}>{name}</span>}
              </span>
            ))}
          </h1>
          <p className="page-subtitle">
            {atTop
              ? `按项目归档，共 ${roots.length} 个项目。点项目进去看成品与素材。`
              : `${dirs.length} 个文件夹 · ${files.length} 个文件（可继续点开子文件夹）`}
          </p>
          {kbScope.focused && (
            <div className="gallery-filters" style={{ marginTop: 6 }}>
              <span style={badge}>知识库 · {persona}</span>
              {kbScope.patterns.slice(0, 3).map((p, i) => <span key={i} style={badge}>{p}</span>)}
              {kbScope.patterns.length > 3 && <span style={badge}>+{kbScope.patterns.length - 3}</span>}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input
              className="field"
              style={{ width: 200, height: 34 }}
              value={kbQ}
              onChange={(e) => setKbQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') runKbQuery(); }}
              placeholder={kbReady ? '检索知识库…' : '知识库未就绪'}
              disabled={!kbReady}
            />
            <button className="btn btn-sm" onClick={runKbQuery} disabled={!kbReady || !kbQ.trim() || kbSearching}><IconSearch size={14} /> {kbSearching ? "检索中…" : "检索"}</button>
          </div>
          <button className="btn btn-sm" onClick={() => setDepositOpen(true)} disabled={!kbReady}><IconBookmark size={14} /> 沉淀</button>
          <button className="btn btn-sm" onClick={load}><IconRefresh size={14} /> 刷新</button>
        </div>
      </div>

      {treeError && <div className="notice-error">{treeError}</div>}

      {/* 项目主题标签（进入项目根时展示） */}
      {atProjectRoot && projectMeta?.tags && projectMeta.tags.length > 0 && (
        <div className="gallery-filters" style={{ marginBottom: 4 }}>
          {projectMeta.tags.map((t) => <span key={t} style={badge}>#{t}</span>)}
        </div>
      )}

      {/* 面包屑返回 + 文件过滤（进入任意层后显示） */}
      {stack.length > 0 && (
        <div className="gallery-filters">
          <button className="btn btn-sm" onClick={() => goTo(stack.length - 1)}>
            <span style={{ transform: 'rotate(180deg)', display: 'inline-flex' }}><IconChevron size={13} /></span> 返回上级
          </button>
          {files.length > 0 && FILTERS.map((f) => (
            <button key={f.key} className={`chip ${filter === f.key ? 'active' : ''}`} onClick={() => setFilter(f.key)}>{f.label}</button>
          ))}
        </div>
      )}

      {kbResults ? (
        <div className="kb-results">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
            <span style={{ fontWeight: 650 }}>{kbResults.total_hits} 条命中 · {kbResults.documents.length} 篇</span>
            {kbResults.source && <span style={badge}>知识库 · {kbResults.source}</span>}
            {kbResults.scoped && <span style={badge}>已按 {persona || '画像'} 聚焦</span>}
            <button className="btn btn-sm" onClick={() => setKbResults(null)} style={{ marginLeft: 'auto' }}>
              <span style={{ transform: 'rotate(180deg)', display: 'inline-flex' }}><IconChevron size={13} /></span> 返回浏览
            </button>
          </div>
          {kbError && <div className="notice-error" style={{ margin: 0 }}>{kbError}</div>}
          {kbResults.available && kbResults.documents.length === 0 && (
            <div className="empty-state" style={{ height: 240 }}>
              <div className="empty-icon"><IconSearch size={40} /></div>
              <p>没有匹配的笔记——换个关键词，或切换画像看不同聚焦。</p>
            </div>
          )}
          <div className="gallery-grid">
            {kbResults.documents.map((doc, i) => {
              const top = doc.hits[0];
              const score = typeof doc.best_score === 'number' ? doc.best_score.toFixed(1) : doc.best_score;
              return (
                <div key={doc.relative_path + '-' + i} className="card card-hover gcard" onClick={() => openKbDoc(doc, kbResults.source || '')}>
                  <div className="gcard-thumb" style={{ alignItems: 'center', justifyContent: 'center' }}>
                    <IconFile size={26} />
                  </div>
                  <div className="gcard-meta" style={{ minWidth: 0 }}>
                    <div className="gcard-name" style={{ fontFamily: "'SF Mono','Consolas',monospace", fontSize: 12, whiteSpace: 'normal', wordBreak: 'break-all' }}>{doc.relative_path}</div>
                    <div className="gcard-sub" style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={badge}>评分 {score}</span>
                      <span style={badge}>{doc.hits.length} 段</span>
                    </div>
                    {top?.snippet && (
                      <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
                        {top.heading && <b style={{ color: 'var(--text)' }}>{top.heading}：</b>}
                        {top.snippet}
                      </div>
                    )}
                    {top && (top.line_start || top.line_end) && (
                      <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>第 {top.line_start}–{top.line_end} 行</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <>
      {extLoading && currentNodes.length === 0 ? (
        <div className="loading" style={{ height: 300 }}><div className="spinner" />加载知识库…</div>
      ) : empty && !treeError ? (
        <div className="empty-state" style={{ height: 300 }}>
          <div className="empty-icon"><IconOutputs size={44} /></div>
          <p>{atTop ? '还没有产物——去对话或技能库生成第一条内容吧' : '这个文件夹是空的'}</p>
        </div>
      ) : hasSplit ? (
        <>
          {/* 成品区 */}
          {deliverableFiles.length > 0 && (
            <>
              <div className="section-label" style={{ margin: '6px 0 8px', fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>
                成品 · {deliverableFiles.length}
              </div>
              <div className="gallery-grid">{deliverableFiles.map(renderFile)}</div>
            </>
          )}
          {/* 素材 / 过程文件区 */}
          {(dirs.length > 0 || restFiles.length > 0) && (
            <>
              <div className="section-label" style={{ margin: '18px 0 8px', fontSize: 13, fontWeight: 600, color: 'var(--text-tertiary)' }}>
                素材 / 过程文件
              </div>
              <div className="gallery-grid">
                {dirs.map(renderDir)}
                {restFiles.map(renderFile)}
              </div>
            </>
          )}
        </>
      ) : (
        <div className="gallery-grid">
          {dirs.map(renderDir)}
          {files.map(renderFile)}
        </div>
      )}
        </>
      )}

      {selected && (
        <div className="drawer-overlay" onClick={() => setSelected(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ minWidth: 0 }}>
                <div className="skill-detail-title" style={{ fontSize: 16 }}>{selected.name}</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 3, fontFamily: "'SF Mono','Consolas',monospace" }}>{selected.path}</div>
              </div>
              <button className="icon-btn" onClick={() => setSelected(null)}>×</button>
            </div>
            <div className="drawer-body">{preview()}</div>
            {!selected.external && (
              <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end' }}>
                <button className="btn btn-sm btn-danger" onClick={(e) => remove(selected, e)}><IconTrash size={13} /> 删除此文件</button>
              </div>
            )}
          </div>
        </div>
      )}

      {depositOpen && (
        <div className="overlay" onClick={() => setDepositOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 540, maxWidth: '92vw' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <div style={{ fontSize: 16, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}><IconBookmark size={15} /> 沉淀到知识库</div>
              <button className="icon-btn" onClick={() => setDepositOpen(false)}>×</button>
            </div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
              <button className={`chip ${depositTab === 'ingest' ? 'active' : ''}`} onClick={() => { setDepositTab('ingest'); setDepositResult(null); }}>从 URL / 文件</button>
              <button className={`chip ${depositTab === 'save' ? 'active' : ''}`} onClick={() => { setDepositTab('save'); setDepositResult(null); }}>保存文本</button>
            </div>

            {depositTab === 'ingest' ? (
              <div style={{ display: 'grid', gap: 12 }}>
                <label style={labelStyle}>URL 或本地文件</label>
                <input className="field" value={ingestForm.source} onChange={(e) => setIngestForm((f) => ({ ...f, source: e.target.value }))} placeholder="https://… 或 /path/to/file.md" />
                <label style={labelStyle}>目标目录（可选，默认：00-收件箱/互联网研究）</label>
                <input className="field" value={ingestForm.target} onChange={(e) => setIngestForm((f) => ({ ...f, target: e.target.value }))} placeholder="留空使用默认收件箱" />
                <label style={labelStyle}>标题（可选）</label>
                <input className="field" value={ingestForm.title} onChange={(e) => setIngestForm((f) => ({ ...f, title: e.target.value }))} placeholder="留空自动取链接标题" />
              </div>
            ) : (
              <div style={{ display: 'grid', gap: 12 }}>
                <label style={labelStyle}>标题</label>
                <input className="field" value={saveForm.title} onChange={(e) => setSaveForm((f) => ({ ...f, title: e.target.value }))} placeholder="笔记标题" />
                <label style={labelStyle}>内容</label>
                <textarea className="field" value={saveForm.content} onChange={(e) => setSaveForm((f) => ({ ...f, content: e.target.value }))} placeholder="要沉淀的文本 / 结论 / 草稿" style={{ minHeight: 140 }} />
                <label style={labelStyle}>目标目录（可选，默认：04-内容生产/00-选题池）</label>
                <input className="field" value={saveForm.target} onChange={(e) => setSaveForm((f) => ({ ...f, target: e.target.value }))} placeholder="留空使用默认目录" />
              </div>
            )}

            {depositResult && (
              <div style={{ marginTop: 14, border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: 14, background: 'var(--bg-elev)' }}>
                {depositResult.error && <div className="notice-error" style={{ margin: 0 }}>{depositResult.error}</div>}
                {!depositResult.error && depositResult.target && (
                  <div style={{ fontSize: 13, marginBottom: 8 }}><b>目标：</b> <span style={{ ...badge, whiteSpace: 'normal' }}>{depositResult.target}</span></div>
                )}
                {depositResult.plan_sha256 && (
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)', fontFamily: "'SF Mono','Consolas',monospace", marginBottom: 8 }}>plan: {depositResult.plan_sha256.slice(0, 20)}…</div>
                )}
                {depositResult.preview && (
                  <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12, color: 'var(--text-secondary)', margin: 0, maxHeight: 200, overflow: 'auto', background: 'var(--surface)', padding: 10, borderRadius: 'var(--radius)' }}>{depositResult.preview}</pre>
                )}
                {depositResult.applied && (
                  <div style={{ marginTop: 10, fontSize: 13, color: 'var(--green)', background: 'var(--green-soft)', border: '1px solid var(--green-soft)', borderRadius: 'var(--radius)', padding: '8px 12px' }}>
                    ✓ 已沉淀 · 事务 {depositResult.transaction_id ? depositResult.transaction_id.slice(0, 16) : '—'}… · 变更 {depositResult.changed?.length ?? 0} 项
                  </div>
                )}
                {depositResult.conflict && (
                  <div className="notice-error" style={{ marginTop: 10, margin: 0 }}>同名冲突（未覆盖）：{depositResult.error || '知识库已存在同名笔记，已拒绝写入。'}</div>
                )}
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 16 }}>
              <button className="btn btn-ghost" onClick={() => setDepositOpen(false)}>取消</button>
              <button className="btn" onClick={() => runDeposit(false)} disabled={depositBusy || (depositTab === 'ingest' ? !ingestForm.source.trim() : !saveForm.title.trim() || !saveForm.content.trim())}>预览</button>
              <button className="btn btn-primary" onClick={() => runDeposit(true)} disabled={depositBusy || (depositTab === 'ingest' ? !ingestForm.source.trim() : !saveForm.title.trim() || !saveForm.content.trim())}>确认沉淀</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
