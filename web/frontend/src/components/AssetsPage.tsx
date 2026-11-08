import { useState, useEffect, useCallback } from 'react';
import type { CSSProperties } from 'react';
import {
  fetchAssetsTree, fetchAssetsChildren, assetsMediaUrl, assetsStatus,
  fetchAssetsSources, assetsAdd, assetsRemove, fetchDirEntries,
} from '../lib/api';
import type { OutputNode, AssetsStatus, DirListing } from '../lib/api';
import { renderMarkdown } from '../lib/sanitize';
import { useAssetOrganization } from '../lib/useAssetOrganization';
import '../styles/assets.css';
import {
  IconLayers, IconFolder, IconImage, IconVideo, IconMusic, IconFile,
  IconRefresh, IconTrash, IconPlus, IconChevron,
} from './icons';

interface Props { persona: string; }

const isHtml = (name: string) => /\.html?$/i.test(name);
const kindIcon = (kind: string | undefined, size = 30) => {
  if (kind === 'video') return <IconVideo size={size} />;
  if (kind === 'audio') return <IconMusic size={size} />;
  if (kind === 'image') return <IconImage size={size} />;
  return <IconFile size={size} />;
};
const badge: CSSProperties = {
  fontSize: 11, padding: '1px 7px', borderRadius: 999,
  background: 'var(--surface-hover)', color: 'var(--text-secondary)', whiteSpace: 'nowrap',
};

/** 递归取目录内第一张图/视频作封面缩略。 */
function firstMedia(n: OutputNode): OutputNode | null {
  if (n.type === 'file') return (n.kind === 'image' || n.kind === 'video') ? n : null;
  for (const c of n.children || []) {
    const m = firstMedia(c);
    if (m) return m;
  }
  return null;
}

/** 素材库：登记素材源目录 → AI 按内容理解整理到分类 → 供视频/文章创作取用（随画像）。 */
export default function AssetsPage({ persona }: Props) {
  const [status, setStatus] = useState<AssetsStatus | null>(null);
  const [sources, setSources] = useState<string[]>([]);
  const [tree, setTree] = useState<OutputNode | null>(null);
  const [loading, setLoading] = useState(true);
  const [dir, setDir] = useState<OutputNode | null>(null);   // 当前展开的目录（null=顶层）
  const [children, setChildren] = useState<OutputNode[] | null>(null);
  const [dirLoading, setDirLoading] = useState(false);
  const [selected, setSelected] = useState<OutputNode | null>(null);
  const [content, setContent] = useState('');
  const [newSrc, setNewSrc] = useState('');
  const [notice, setNotice] = useState('');
  const [pickOpen, setPickOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, src, t] = await Promise.all([
        assetsStatus(persona),
        fetchAssetsSources(persona),
        fetchAssetsTree(persona),
      ]);
      setStatus(s);
      setSources(src.sources);
      setTree(t);
      setDir(null);
      setChildren(null);
    } catch (e) {
      setNotice((e as Error).message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, [persona]);
  const organization = useAssetOrganization(persona, load);

  useEffect(() => { load(); }, [load]);

  const register = async () => {
    if (organization.busy) return;
    const d = newSrc.trim();
    if (!d) return;
    try {
      const r = await assetsAdd(d, persona);
      setNewSrc('');
      const parts = [r.added.length ? `+${r.added.length} 已登记` : '', r.already.length ? `${r.already.length} 重复` : ''];
      if (r.invalid.length) parts.push(`${r.invalid.length} 无效`);
      setNotice(parts.filter(Boolean).join(' · ') || '已登记');
      await load();
      if (r.added.length) await organization.start();
    } catch (e) {
      setNotice((e as Error).message || '登记失败');
    }
  };
  const removeSrc = async (s: string) => {
    try { await assetsRemove(s, persona); await load(); } catch (e) { setNotice((e as Error).message || '注销失败'); }
  };

  // 展开某目录的子节点（懒加载）。
  const openDir = async (n: OutputNode) => {
    setDir(n);
    setDirLoading(true);
    setChildren(null);
    try { setChildren(await fetchAssetsChildren(n.path, persona)); }
    catch (e) { setNotice((e as Error).message || '加载失败'); }
    finally { setDirLoading(false); }
  };
  const toTop = () => { setDir(null); setChildren(null); };

  // 预览文件：媒体走 /api/assets/media；文本/HTML 拉取内容渲染。
  const previewFile = async (f: OutputNode) => {
    setSelected(f);
    if (f.kind === 'text') {
      try { const r = await fetch(assetsMediaUrl(f.path, persona)); setContent(await r.text()); }
      catch { setContent(''); }
    }
  };
  const mediaUrl = (f: OutputNode) => assetsMediaUrl(f.path, persona);

  const cats = tree?.children ?? [];
  const dirs = (children ?? []).filter((c) => c.type === 'dir');
  const files = (children ?? []).filter((c) => c.type === 'file');

  return (
    <div className="gallery-page asset-library-page">
      <div className="gallery-head">
        <div>
          <h1 className="page-title"><IconLayers size={21} /> 素材库</h1>
          <p className="page-subtitle">
            登记素材源目录（图片 / 视频 / 文本），AI 按内容理解自动整理到分类，供视频、文章创作取用。
            {status && <span style={{ marginLeft: 8 }}>
              <span style={badge}>{status.organized} 项已整理</span>
              <span style={badge}>{status.categories} 分类</span>
              {status.registered ? <span style={badge}>已登记 {status.sources?.length ?? 0} 源</span> : <span style={badge}>未登记</span>}
            </span>}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <button className="btn btn-sm" onClick={organization.start} disabled={organization.busy || !persona || !sources.length}
            style={{ minWidth: 138 }}><IconRefresh size={14} /> {organization.busy ? '正在理解素材…' : '整理素材'}</button>
          <button className="btn btn-sm" onClick={load}><IconRefresh size={14} /> 刷新</button>
        </div>
      </div>

      {notice && <div className="notice-error" style={{ background: 'var(--surface-hover)' }}>{notice}</div>}
      {organization.error && <div className="notice-error" role="alert">{organization.error}</div>}
      {organization.job && <div role="status" aria-live="polite" style={{ color: 'var(--text-secondary)', padding: '8px 0' }}>
        {organization.job.status === 'running'
          ? `正在理解与归类：${organization.job.completed} / ${organization.job.total} 项。离开页面后任务继续运行。`
          : organization.job.status === 'done' ? `整理完成：${organization.job.placed} 项已归类` : `整理中断：已完成 ${organization.job.completed} / ${organization.job.total} 项，可重试。`}
      </div>}

      {/* 登记素材源 */}
      <div className="card" style={{ padding: 14, marginTop: 6 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={badge}>登记素材源目录</span>
          <input
            className="field"
            style={{ flex: 1, minWidth: 'min(260px, 100%)', height: 34 }}
            value={newSrc}
            onChange={(e) => setNewSrc(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') register(); }}
            placeholder="本机目录路径，如 /Users/you/素材/ 或某个文件夹"
          />
          <button className="btn btn-sm" onClick={register} disabled={organization.busy || !newSrc.trim() || !persona}><IconPlus size={14} /> 登记</button>
          <button className="btn btn-sm" onClick={() => setPickOpen(true)}><IconFolder size={14} /> 选择目录</button>
        </div>
        {sources.length > 0 && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
            {sources.map((s) => (
              <span key={s} className="asset-source" style={{ ...badge, whiteSpace: 'normal', background: 'var(--surface-hover)' }}>
                {s}
                <button title="注销" onClick={() => removeSrc(s)} disabled={organization.busy}
                  style={{ marginLeft: 6, border: 'none', background: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 0 }}>
                  <IconTrash size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 浏览 */}
      <div style={{ marginTop: 16 }}>
        <div className="crumb" style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
          <button type="button" className="crumb" onClick={toTop}>素材库</button>
          {dir && <><span className="crumb-sep">/</span><span>{dir.name}</span></>}
        </div>

        {loading ? <div className="dash-empty">加载中…</div>
          : !tree ? <div className="dash-empty">还没有素材——在上面登记一个素材源目录，再点「整理素材」让 AI 按内容理解归类。</div>
          : dir ? (
            dirLoading ? <div className="dash-empty">加载中…</div>
              : (dirs.length === 0 && files.length === 0) ? <div className="dash-empty">这个分类是空的</div>
              : <div className="gallery-grid">
                  {dirs.map((c) => (
                    <button type="button" key={c.path} className="card card-hover gcard" onClick={() => openDir(c)}>
                      <div className="gcard-thumb">
                        <span className="gcard-kind">文件夹</span>
                        {(() => { const m = firstMedia(c); return m ? <Thumb f={m} url={mediaUrl(m)} /> : <div className="gcard-ph"><IconFolder size={38} /></div>; })()}
                      </div>
                      <div className="gcard-meta">
                        <div className="gcard-name">{c.name}</div>
                        <div className="gcard-sub"><span>{c.fileCount ?? 0} 个文件</span></div>
                      </div>
                    </button>
                  ))}
                  {files.map((c) => (
                    <button type="button" key={c.path} className="card card-hover gcard" onClick={() => previewFile(c)}>
                      <div className="gcard-thumb">
                        <span className="gcard-kind">{kindIcon(c.kind)}</span>
                        <Thumb f={c} url={mediaUrl(c)} />
                      </div>
                      <div className="gcard-meta"><div className="gcard-name" title={c.name}>{c.name}</div></div>
                    </button>
                  ))}
                </div>
          ) : (
            <div className="gallery-grid">
              {cats.map((c) => (
                <button type="button" key={c.path} className="card card-hover gcard" onClick={() => openDir(c)}>
                  <div className="gcard-thumb">
                    <span className="gcard-kind">分类</span>
                    {(() => { const m = firstMedia(c); return m ? <Thumb f={m} url={mediaUrl(m)} /> : <div className="gcard-ph"><IconFolder size={38} /></div>; })()}
                  </div>
                  <div className="gcard-meta">
                    <div className="gcard-name">{c.name}</div>
                    <div className="gcard-sub"><span>{c.fileCount ?? 0} 个文件</span></div>
                  </div>
                </button>
              ))}
            </div>
          )}
      </div>

      {/* 预览 */}
      {selected && (
        <div className="overlay" onClick={() => setSelected(null)}>
          <div className="modal" style={{ width: 640, maxWidth: '100%', height: '78vh', overflow: 'auto', padding: 18 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <strong style={{ fontSize: 15 }}>{selected.name}</strong>
              <button className="btn btn-sm" onClick={() => setSelected(null)}>关闭</button>
            </div>
            <Preview f={selected} url={mediaUrl(selected)} content={content} kind={selected.kind} />
          </div>
        </div>
      )}

      {/* 选择素材源目录 */}
      {pickOpen && (
        <DirPickerModal
          onClose={() => setPickOpen(false)}
          onPick={(d) => { setPickOpen(false); setNewSrc(d); }}
        />
      )}
    </div>
  );
}

/** 目录选择器：点击选择本机素材源目录（后端 /api/assets/pick-dir 只读浏览），选中后填回输入框再「登记」。 */
function DirPickerModal({ onClose, onPick }: { onClose: () => void; onPick: (path: string) => void }) {
  const [listing, setListing] = useState<DirListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const load = useCallback((path: string) => {
    setLoading(true);
    setErr('');
    fetchDirEntries(path)
      .then((d) => setListing(d))
      .catch((e) => setErr((e as Error).message || '读取失败'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(''); }, [load]);

  const canUp = !!listing && listing.parent !== listing.path;
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal dir-picker" onClick={(e) => e.stopPropagation()}>
        <div className="dir-picker-head">
          <strong style={{ fontSize: 15 }}><IconFolder size={16} /> 选择素材源目录</strong>
          <button className="btn btn-sm" onClick={onClose}>关闭</button>
        </div>
        <div className="dir-picker-path" title={listing?.path}>{listing?.path || '…'}</div>
        <div className="dir-picker-body">
          {loading ? <div className="dash-empty">加载中…</div>
            : err ? <div className="notice-error" style={{ background: 'var(--surface-hover)' }}>{err}</div>
            : (listing && listing.dirs.length === 0) ? <div className="dash-empty">此目录下没有子文件夹——可直接「选择此目录」。</div>
            : (
              <div className="dir-picker-grid">
                {listing?.dirs.map((d) => (
                  <div key={d.path} className="card card-hover dir-picker-item" onClick={() => load(d.path)}>
                    <IconFolder size={18} />
                    <span className="dir-picker-name">{d.name}</span>
                    <IconChevron size={15} className="dir-picker-chevron" />
                  </div>
                ))}
              </div>
            )}
        </div>
        {listing?.quick.length ? (
          <div className="dir-picker-quick">
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>常用</span>
            {listing.quick.map((d) => (
              <button key={d.path} className="btn btn-sm" onClick={() => load(d.path)}>{d.name}</button>
            ))}
          </div>
        ) : null}
        <div className="dir-picker-foot">
          <button className="btn btn-sm" onClick={() => canUp && load(listing!.parent)} disabled={!canUp}>返回上级</button>
          <button className="btn btn-sm btn-primary" onClick={() => onPick(listing?.path || '')} disabled={!listing}>选择此目录</button>
        </div>
      </div>
    </div>
  );
}

function Thumb({ f, url }: { f: OutputNode; url: string }) {
  if (f.kind === 'image') return <img src={url} alt={f.name} style={{ width: '100%', height: 100, objectFit: 'cover', borderRadius: 6 }} />;
  if (f.kind === 'video') return <div style={{ position: 'relative', width: '100%', height: 100, background: '#000', borderRadius: 6, overflow: 'hidden' }}><video src={url} style={{ width: '100%', height: '100%', objectFit: 'cover' }} muted /><span style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff' }}><IconVideo size={30} /></span></div>;
  return <div className="gcard-ph">{kindIcon(f.kind, 38)}</div>;
}

function Preview({ f, url, content, kind }: { f: OutputNode; url: string; content: string; kind?: string }) {
  if (kind === 'image') return <img src={url} alt={f.name} style={{ maxWidth: '100%', borderRadius: 'var(--radius)' }} />;
  if (kind === 'video') return <video src={url} controls style={{ maxWidth: '100%', borderRadius: 'var(--radius)' }} />;
  if (kind === 'audio') return <audio src={url} controls style={{ width: '100%' }} />;
  if (kind === 'text') {
    if (isHtml(f.name)) return <iframe src={url} title={f.name} sandbox="allow-scripts" allow="clipboard-write" style={{ width: '100%', height: '68vh', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: '#fff' }} />;
    return <div className="outputs-viewer-content" style={{ whiteSpace: 'pre-wrap' }}>{content ? renderMarkdown(content) : ''}</div>;
  }
  return <div style={{ color: 'var(--text-secondary)', fontSize: 14 }}>无法预览。<a href={url} download style={{ color: 'var(--accent-start)' }}>下载 {f.name}</a></div>;
}
