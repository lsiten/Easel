import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchOutputs, launchOpenfilmStudio, mediaUrl } from '../lib/api';
import type { OutputNode } from '../lib/api';
import { videoProjects } from '../lib/contentOrganization';
import type { VideoProject } from '../lib/contentOrganization';
import { useOpenfilmStudio } from '../lib/useOpenfilmStudio';
import { IconClose, IconExternal, IconRefresh, IconVideo } from './icons';
import HypitReplicationWorkspace from './HypitReplicationWorkspace';

interface Props {
  session?: string;
  persona: string;
  onMakeVideo: () => void;
  mode?: 'studio' | 'library';
}

function fileSize(bytes?: number): string {
  if (!bytes || bytes < 0) return '';
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

function launchFailure(reason: string): string {
  if (reason === 'no-project') return '没有可启动的视频项目。请先打开一个视频项目，或创建一支新视频。';
  if (reason === 'not-ready') return '视频制作环境尚未就绪，请检查 OpenFilm 安装后重试。';
  return 'Studio 未能启动，请检查视频制作环境后重试。';
}

function collectVideoFiles(node: OutputNode): OutputNode[] {
  if (node.type === 'file') return node.kind === 'video' ? [node] : [];
  return (node.children ?? []).flatMap(collectVideoFiles);
}

function normalizeProjects(items: readonly unknown[]): VideoProject[] {
  return items.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    if ('node' in item && item.node && typeof item.node === 'object') return [item as VideoProject];
    const node = item as OutputNode;
    if (node.type !== 'dir') return [];
    const files = collectVideoFiles(node);
    return [{
      node,
      cover: files[0] ?? null,
      video: files[0] ?? null,
      videoCount: files.length,
      fileCount: node.fileCount ?? files.length,
    }];
  });
}

export default function VideoPage({ session, persona, onMakeVideo, mode = 'studio' }: Props) {
  const { studio, loading: studioLoading, error: studioFailure, refresh } = useOpenfilmStudio({ session, persona });
  const scope = JSON.stringify([session ?? null, persona]);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{
    readonly scope: string;
    readonly videos: VideoProject[];
    readonly notice: string;
  } | null>(null);
  const [preview, setPreview] = useState<VideoProject | null>(null);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState('');
  const pageRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  const load = useCallback(() => {
    setResult(null);
    setLaunchError('');
    setRevision((value) => value + 1);
    refresh();
  }, [refresh]);

  const closePreview = useCallback(() => {
    setPreview(null);
  }, []);

  useEffect(() => {
    if (preview || !openerRef.current) return;
    const opener = openerRef.current;
    const timer = window.setTimeout(() => opener.focus({ preventScroll: true }), 0);
    return () => window.clearTimeout(timer);
  }, [preview]);

  useEffect(() => {
    const controller = new AbortController();
    fetchOutputs(persona, controller.signal).then((roots) => {
      if (!controller.signal.aborted) {
        setResult({
          scope,
          videos: videoProjects(roots).sort((a, b) => (b.node.mtime ?? 0) - (a.node.mtime ?? 0)),
          notice: '',
        });
      }
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setResult({ scope, videos: [], notice: error instanceof Error ? error.message : '视频项目加载失败。' });
      }
    });
    return () => controller.abort();
  }, [scope, persona, revision]);

  useEffect(() => {
    if (!preview) return;
    const page = pageRef.current;
    const previousOverflow = page?.style.overflowY ?? '';
    if (page) page.style.overflowY = 'hidden';
    closeRef.current?.focus({ preventScroll: true });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closePreview();
        return;
      }
      if (event.key !== 'Tab') return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = [...dialog.querySelectorAll<HTMLElement>('button, a, video[controls]')]
        .filter((element) => !element.hasAttribute('disabled'));
      const first = focusable[0];
      const last = focusable.at(-1);
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      if (page) page.style.overflowY = previousOverflow;
    };
  }, [closePreview, preview]);

  function openPreview(item: VideoProject, opener: HTMLElement) {
    openerRef.current = opener;
    setPreview(item);
  }

  async function startStudio() {
    if (launching) return;
    setLaunching(true);
    setLaunchError('');
    try {
      const launched = await launchOpenfilmStudio(undefined, session, persona);
      if (launched.launched) refresh();
      else setLaunchError(launchFailure(launched.reason || ''));
    } catch (error) {
      setLaunchError(error instanceof Error ? error.message : '无法启动 Studio，请稍后重试。');
    } finally {
      setLaunching(false);
    }
  }

  if (result && result.scope !== scope) setResult(null);
  const current = result?.scope === scope ? result : null;
  const videos = normalizeProjects(current?.videos ?? []);
  const loading = !current;
  const notice = current?.notice ?? '';
  const scopedStudio = studio?.studio && preview && preview.node.path.split('/')[0] === studio.topic ? studio.studio : '';
  const studioReady = Boolean(studio?.available);
  const studioMessage = studioFailure || launchError;
  const totalVideoCount = videos.reduce((total, project) => total + project.videoCount, 0);

  return <div className="video-page-shell">
    <div ref={pageRef} className="gallery-page video-gallery-page">
      <header className="gallery-head video-page-head">
        <div>
          <h1 className="page-title"><IconVideo size={21} /> {mode === 'studio' ? '视频制作' : '视频项目'}</h1>
          <p className="page-subtitle">{mode === 'studio'
            ? '由 AI 驱动 OpenFilm 制作视频，可在 Studio 中继续编辑，并从素材库取用图片、视频和文本。'
            : '集中查看 OpenFilm、Hypit 与历史生产流程生成的视频项目和成品。'}</p>
        </div>
        <div className="video-page-actions">
          <button type="button" className="btn btn-sm" onClick={load} disabled={loading}>
            <IconRefresh size={14} /> 刷新
          </button>
          <button type="button" className="btn btn-primary" onClick={onMakeVideo}>
            <IconVideo size={14} /> {mode === 'studio' ? '用 AI 做一支视频' : '去创作视频'}
          </button>
        </div>
      </header>

      <div className="video-page-content">
        <section className={`video-studio-status ${studioMessage ? 'has-error' : studioReady ? 'is-ready' : ''}`} aria-labelledby="video-studio-status-heading">
          <div className="video-studio-status-main">
            <span className="video-studio-status-icon" aria-hidden><IconVideo size={18} /></span>
            <div>
              <h2 id="video-studio-status-heading">OpenFilm Studio</h2>
              <p role="status" aria-live="polite">
                {studioLoading && !studio ? '正在检测制作环境…'
                  : studioFailure ? `检测失败：${studioFailure}`
                    : studioReady && studio?.studio ? '当前项目已连接，可以打开编辑器继续制作。'
                      : studioReady ? '制作环境已就绪，尚未启动当前项目的 Studio。'
                        : '尚未检测到可用的 OpenFilm 制作环境。'}
              </p>
              {(studio?.openfilm || studio?.runner?.length) && <span className="video-studio-meta">
                {studio?.openfilm ? `引擎 ${studio.openfilm}` : ''}{studio?.runner?.length ? ` · ${studio.runner[0]}` : ''}
              </span>}
            </div>
          </div>
          <div className="video-studio-actions">
            {studio?.studio && <a className="btn btn-sm" href={studio.studio} target="_blank" rel="noreferrer">
              打开 Studio <IconExternal size={13} />
            </a>}
            {studioReady && !studio?.studio && <button type="button" className="btn btn-sm" disabled={launching} onClick={() => void startStudio()}>
              {launching ? '启动中…' : '启动 Studio'}
            </button>}
            {!studioLoading && !studioReady && !studioFailure && <span className="video-studio-hint">运行 <code>easel setup</code> 安装视频制作套件。</span>}
          </div>
          {studioMessage && <div className="video-inline-error" role="alert">{studioMessage}</div>}
        </section>

        {notice && <div className="video-inline-error" role="alert">
          <span>{notice}</span><button type="button" className="btn btn-sm" onClick={load}>重试</button>
        </div>}

        <section className="video-collection" aria-labelledby="video-collection-heading" aria-busy={loading}>
          <div className="video-collection-head">
            <div><h2 id="video-collection-heading">视频项目</h2><p>每个项目只展示一次；点击项目封面预览成片，进入 Studio 后可继续编辑。</p></div>
            {!loading && <span className="video-count">{videos.length} 个项目 · {totalVideoCount} 个视频</span>}
          </div>

          {loading ? <div className="video-card-grid" aria-label="正在加载视频项目">
            {[0, 1, 2].map((item) => <div className="video-card video-card-skeleton" key={item} aria-hidden>
              <div className="video-skeleton-media" /><div className="video-skeleton-line" /><div className="video-skeleton-line short" />
            </div>)}
          </div>
            : notice ? null
              : videos.length === 0 ? <div className="video-empty">
                <span className="video-empty-icon" aria-hidden><IconVideo size={24} /></span>
                <div><h3>还没有视频</h3><p>让 AI 从素材库规划并制作一支视频，完成后会出现在这里。</p></div>
                <button type="button" className="btn btn-primary" onClick={onMakeVideo}><IconVideo size={14} /> 开始制作</button>
              </div>
                : <div className="video-card-grid">
                  {videos.map((item) => {
                    const project = item.node;
                    const title = project.meta?.title || project.name;
                    const projectFolder = project.path?.split('/')[0] || project.name || '未分组';
                    const editable = Boolean(project.openfilmProject && studio?.studio && projectFolder === studio.topic);
                    const previewSource = item.cover?.kind === 'video' ? item.cover : item.video;
                    const poster = item.cover?.kind === 'image' ? mediaUrl(item.cover.path) : undefined;
                    const details = [
                      `${item.videoCount} 个视频`,
                      `${item.fileCount} 个文件`,
                      project.meta?.platform,
                    ].filter(Boolean).join(' · ');
                    return <article key={project.path || project.name} className="video-card">
                      <button type="button" className="video-card-media" aria-label={item.video ? `预览 ${title}` : title}
                        disabled={!item.video} onClick={(event) => openPreview(item, event.currentTarget)}>
                        {previewSource
                          ? <video src={mediaUrl(previewSource.path)} poster={poster} muted preload="metadata" aria-hidden />
                          : poster ? <img src={poster} alt="" loading="lazy" />
                            : <span className="video-media-unavailable">暂无封面</span>}
                        <span className="video-card-badge">{project.catalog?.projectKindLabel || '视频项目'}</span>
                        {item.video && <span className="video-card-play" aria-hidden><IconVideo size={21} /></span>}
                      </button>
                      <div className="gcard-meta">
                        <div className="video-card-copy">
                          <h3 className="gcard-name" title={title}>{title}</h3>
                          <p>{details}{item.video && fileSize(item.video.size) ? ` · ${fileSize(item.video.size)}` : ''}</p>
                        </div>
                        <div className="video-card-actions">
                          {item.video && <button type="button" className="video-card-preview" onClick={(event) => openPreview(item, event.currentTarget)}>预览</button>}
                          {editable && <a href={studio?.studio || '#'} target="_blank" rel="noreferrer">
                            Studio <IconExternal size={12} />
                          </a>}
                        </div>
                      </div>
                    </article>;
                  })}
                </div>}
        </section>

        {mode === 'studio' && <HypitReplicationWorkspace session={session} persona={persona} />}
      </div>
    </div>

    {preview?.video && <div className="video-preview-layer">
      <button type="button" className="video-preview-backdrop" tabIndex={-1} aria-label="关闭视频预览" onClick={closePreview} />
      <div ref={dialogRef} className="video-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="video-preview-title">
        <header>
          <div><span>项目成片</span><h2 id="video-preview-title">{preview.node.meta?.title || preview.node.name}</h2></div>
          <button ref={closeRef} type="button" className="video-preview-close" aria-label="关闭视频预览" onClick={closePreview}>
            <IconClose size={18} />
          </button>
        </header>
        <div className="video-preview-stage">
          <video key={preview.video.path} src={mediaUrl(preview.video.path)} controls preload="metadata" />
        </div>
        <footer>
          <span>{preview.video.path}</span>
          <div>
            <a className="btn btn-sm" href={mediaUrl(preview.video.path)} target="_blank" rel="noreferrer">打开原文件</a>
            {scopedStudio && <a className="btn btn-sm" href={scopedStudio} target="_blank" rel="noreferrer">在 Studio 中编辑 <IconExternal size={12} /></a>}
          </div>
        </footer>
      </div>
    </div>}
  </div>;
}
