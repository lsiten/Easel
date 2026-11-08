import { useState, useEffect, useCallback } from 'react';
import { fetchOutputs, mediaUrl } from '../lib/api';
import type { OutputNode } from '../lib/api';
import { useOpenfilmStudio } from '../lib/useOpenfilmStudio';
import { IconVideo, IconRefresh } from './icons';

interface Props { session?: string; persona: string; onMakeVideo: () => void; }

/** 收集所有视频文件（带其所属 openfilm 项目标记）。 */
function collectVideos(roots: OutputNode[], ofAncestor = false): { node: OutputNode; ofProject: boolean }[] {
  const out: { node: OutputNode; ofProject: boolean }[] = [];
  for (const n of roots) {
    if (n.type === 'file') {
      if (n.kind === 'video') out.push({ node: n, ofProject: ofAncestor || !!n.openfilmProject });
      continue;
    }
    const childOf = ofAncestor || !!n.openfilmProject;
    out.push(...collectVideos(n.children ?? [], childOf));
  }
  return out;
}

/** 视频制作：由 AI 驱动 OpenFilm 制作视频，Studio 可视化编辑；素材来自素材库。 */
export default function VideoPage({ session, persona, onMakeVideo }: Props) {
  const { studio, refresh } = useOpenfilmStudio({ session, persona });
  const scope = JSON.stringify([session ?? null, persona]);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{
    readonly scope: string;
    readonly videos: ReturnType<typeof collectVideos>;
    readonly notice: string;
  } | null>(null);
  const load = useCallback(() => { setResult(null); setRevision(value => value + 1); refresh(); }, [refresh]);
  useEffect(() => {
    const controller = new AbortController();
    fetchOutputs(persona, controller.signal).then(roots => {
      if (!controller.signal.aborted) setResult({ scope, videos: collectVideos(roots), notice: '' });
    }).catch(error => {
      if (!controller.signal.aborted) setResult({ scope, videos: [], notice: error instanceof Error ? error.message : '加载失败' });
    });
    return () => controller.abort();
  }, [scope, persona, revision]);
  if (result && result.scope !== scope) setResult(null);
  const current = result?.scope === scope ? result : null;
  const videos = current?.videos ?? [];
  const loading = !current;
  const notice = current?.notice ?? '';

  return (
    <div className="gallery-page">
      <div className="gallery-head">
        <div>
          <h1 className="page-title"><IconVideo size={21} /> 视频制作</h1>
          <p className="page-subtitle">由 AI 驱动 OpenFilm 制作视频，Studio 可视化编辑；可从素材库取用图片 / 视频 / 文本。</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <button className="btn btn-sm" onClick={load}><IconRefresh size={14} /> 刷新</button>
          <button className="btn btn-sm" onClick={onMakeVideo}><IconVideo size={14} /> 用 AI 做一支视频</button>
        </div>
      </div>

      {notice && <div className="notice-error" style={{ background: 'var(--surface-hover)' }}>{notice}</div>}

      {/* 能力 / Studio 状态 */}
      <div className="card" style={{ padding: 16, marginTop: 6 }}>
        {studio ? (studio.available ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, color: 'var(--green)' }}>● OpenFilm 就绪</span>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {studio.openfilm ? `引擎 ${studio.openfilm}` : ''}{studio.runner?.length ? ` · ${studio.runner[0]}` : ''}
            </span>
            {studio.studio && (
              <a href={studio.studio} target="_blank" rel="noreferrer"
                className="btn btn-sm" style={{ marginLeft: 'auto' }}>在 Studio 中编辑 ↗</a>
            )}
          </div>
        ) : (
          <div style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
            OpenFilm 未就绪。运行 <code>easel setup</code> 安装视频制作套件，或在「设置 · 环境」里安装后再来制作视频。
          </div>
        )) : <div className="dash-empty">检测中…</div>}
      </div>

      {/* 已制作的视频 */}
      <div style={{ marginTop: 16 }}>
        <h2 style={{ fontSize: 16, marginBottom: 10 }}>已制作的视频</h2>
        {loading ? <div className="dash-empty">加载中…</div>
          : videos.length === 0 ? (
            <div className="dash-empty">还没有视频——点「用 AI 做一支视频」，或去对话里让 AI 用素材库的素材做一支。</div>
          ) : (
            <div className="outputs-grid">
              {videos.map(({ node: v, ofProject }) => (
                <div key={v.path} className="card card-hover gcard">
                  <div className="gcard-thumb">
                    <span className="gcard-kind">视频</span>
                    <div style={{ position: 'relative', width: '100%', height: 120, background: '#000', borderRadius: 6, overflow: 'hidden' }}>
                      <video src={mediaUrl(v.path)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} muted />
                      <span style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff' }}><IconVideo size={30} /></span>
                    </div>
                  </div>
                  <div className="gcard-meta">
                    <div className="gcard-name" title={v.name}>{v.name}</div>
                    {ofProject && studio?.studio && v.path.split('/')[0] === studio.topic && (
                      <div className="gcard-sub">
                        <a href={studio.studio} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-start)', fontSize: 12 }}>在 Studio 中编辑 ↗</a>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
      </div>
    </div>
  );
}
