import { useEffect, useRef, useState } from 'react';
import { artifactFromUrl, fitArtifact, mergeArtifacts } from '../lib/artifacts';
import { fetchArtifactArchive, mediaUrl } from '../lib/api';
import { useOpenfilmStudio } from '../lib/useOpenfilmStudio';
import type { Artifact } from '../lib/artifacts';
import { IconChevron, IconOutputs } from './icons';

function HtmlArtifact({ artifact }: { artifact: Artifact }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [source, setSource] = useState('');
  const [error, setError] = useState(false);
  const [bounds, setBounds] = useState({ width: 800, height: 600 });
  const [available, setAvailable] = useState({ width: 640, height: 480 });
  const token = useRef(crypto.randomUUID());
  useEffect(() => {
    const controller = new AbortController();
    void fetch(artifact.url, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('preview');
      const html = await response.text();
      const base = new URL('.', new URL(artifact.url, window.location.origin)).href;
      const document = new DOMParser().parseFromString(html, 'text/html');
      document.querySelectorAll('base').forEach(element => element.remove());
      const baseElement = document.createElement('base');
      baseElement.href = base;
      document.head.prepend(baseElement);
      const script = document.createElement('script');
      script.textContent = `(() => {
        const report = () => parent.postMessage({type:'easel-artifact-size',token:${JSON.stringify(token.current)},
          width:Math.max(document.documentElement.scrollWidth,document.body.scrollWidth),
          height:Math.max(document.documentElement.scrollHeight,document.body.scrollHeight)}, '*');
        addEventListener('load', report);
        new ResizeObserver(report).observe(document.body);
        document.fonts?.ready.then(report);
        report();
      })();`;
      document.body.append(script);
      if (!controller.signal.aborted) setSource('<!doctype html>' + document.documentElement.outerHTML);
    }).catch(() => {
      if (!controller.signal.aborted) setError(true);
    });
    return () => controller.abort();
  }, [artifact.url]);
  useEffect(() => {
    const observer = new ResizeObserver(entries => {
      const rect = entries[0].contentRect;
      setAvailable({ width: rect.width, height: rect.height });
    });
    if (stage.current) observer.observe(stage.current);
    const resize = (event: MessageEvent) => {
      const data = event.data;
      if (event.source !== frame.current?.contentWindow || data?.type !== 'easel-artifact-size'
          || data.token !== token.current || !Number.isFinite(data.width) || !Number.isFinite(data.height)
          || data.width <= 0 || data.height <= 0 || data.width > 20000 || data.height > 50000) return;
      setBounds(previous => previous.width === data.width && previous.height === data.height
        ? previous : { width: data.width, height: data.height });
    };
    window.addEventListener('message', resize);
    return () => { observer.disconnect(); window.removeEventListener('message', resize); };
  }, []);
  const fitted = fitArtifact(bounds.width, bounds.height, available.width - 24, available.height - 24);
  return (
    <div className="artifact-html-stage" ref={stage}>
      {error ? <span className="artifact-hint">预览加载失败，可在新标签打开原文件</span>
        : !source ? <span className="artifact-hint">正在加载预览…</span>
        : <div className="artifact-html-fit" style={{ width: fitted.width, height: fitted.height }}>
          <iframe ref={frame} srcDoc={source} title={artifact.name} sandbox="allow-scripts" allow="clipboard-write"
            style={{ width: bounds.width, height: bounds.height, transform: `scale(${fitted.scale})` }} />
        </div>}
    </div>
  );
}

/** 海报（首帧）兜底：视频播放彻底失败时展示首帧，而不是永久「预览加载失败」；
 *  海报本身也加载不了则回落提示，可打开/下载原文件。 */
function PosterOrHint({ poster, name }: { poster: string | null; name: string }) {
  const [imgFailed, setImgFailed] = useState(false);
  if (!poster || imgFailed) return <div className="artifact-hint">预览加载失败，可打开或下载原文件</div>;
  return <img className="artifact-poster" src={poster} alt={name}
    onError={() => setImgFailed(true)} />;
}

function ArtifactPreview({ artifact }: { artifact: Artifact }) {
  // 视频/音频/图片偶发 onError（文件仍在渲染、瞬时网络抖动、首帧加载竞态）会让预览
  // 永久卡死（error 只增不减）—— 按次数退避重试；仍失败则回落到海报（首帧）兜底，
  // 最后才提示「打开原文件」。
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setAttempt(0); setFailed(false); }, [artifact.url]);
  const maxAttempts = 4;
  const onReady = () => { setFailed(false); };
  const onError = () => {
    if (attempt >= maxAttempts - 1) setFailed(true);
    else {
      const next = attempt + 1;
      window.setTimeout(() => setAttempt(next), 700 * next);
    }
  };
  if (artifact.kind === 'html') return <HtmlArtifact artifact={artifact} />;
  // 从 /api/media/<rel> 推导 /api/media/poster/<rel>（海报由后端惰性抽帧生成并缓存）。
  const poster = artifact.kind === 'video' && artifact.url.startsWith('/api/media/')
    ? artifact.url.replace(/^\/api\/media\//, '/api/media/poster/') : null;
  if (failed) return <PosterOrHint poster={poster} name={artifact.name} />;
  if (artifact.kind === 'image')
    return <img key={attempt} src={artifact.url} alt={artifact.name} onError={onError} onLoad={onReady} />;
  if (artifact.kind === 'video')
    return <video key={attempt} src={artifact.url} poster={poster || undefined} controls preload="metadata"
      onLoadedMetadata={onReady} onCanPlay={onReady} onError={onError} />;
  if (artifact.kind === 'audio')
    return <audio key={attempt} src={artifact.url} controls preload="metadata"
      onLoadedMetadata={onReady} onCanPlay={onReady} onError={onError} />;
  return <iframe key={attempt} src={artifact.url} title={artifact.name} sandbox="" />;
}

/** 消息内联的默认产物：OpenFilm film 预览（播放、无 timeline）。
 *  与右侧侧边栏的编辑视角（studio/editor，带 timeline）区分：消息里看成片播放，右侧看制作/编辑过程。
 *  仅当用户要导出/发布出 mp4 时，创作产物才改显示 mp4。 */
function StudioPreviewGallery({ sessionKey, persona }: { sessionKey?: string; persona?: string }) {
  const { studio: info } = useOpenfilmStudio({ session: sessionKey, persona }, 5000);
  const studio = info?.player || info?.film || info?.studio || '';
  return (
    <section className="artifact-gallery" aria-label="创作产物（OpenFilm 预览）">
      <div className="artifact-header"><IconOutputs size={16} /><span>创作产物</span>
        <span className="artifact-count" aria-live="polite">OpenFilm 预览</span></div>
      <div className="artifact-stage">
        {studio
          ? <iframe key={`${studio}:${info?.rev || ''}`} src={studio} title="OpenFilm 预览" sandbox="allow-scripts allow-same-origin allow-popups" />
          : <div className="artifact-hint">OpenFilm 预览准备中…</div>}
      </div>
      <div className="artifact-footer">
        <span className="artifact-name" title="OpenFilm 预览（成片播放；编辑/制作过程见右侧）">OpenFilm 预览</span>
        {studio && <a href={studio} target="_blank" rel="noreferrer">打开 ↗</a>}
      </div>
    </section>
  );
}

export default function ArtifactGallery({ artifacts, archives, isVideoSession, videoProduct, sessionKey, persona }: { artifacts: Artifact[]; archives?: string[]; isVideoSession?: boolean; videoProduct?: boolean; sessionKey?: string; persona?: string }) {
  const [index, setIndex] = useState(0);
  const [archiveArtifacts, setArchiveArtifacts] = useState<Artifact[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    setArchiveArtifacts([]);
    if (archives?.length) {
      void Promise.all(archives.slice(0, 8).map(path =>
        fetchArtifactArchive(path, controller.signal).then(result =>
          result.paths.map(path => artifactFromUrl(mediaUrl(path))).filter((artifact): artifact is Artifact => !!artifact),
        ).catch(() => []),
      )).then(groups => {
        if (!controller.signal.aborted) setArchiveArtifacts(mergeArtifacts(...groups));
      });
    }
    return () => controller.abort();
  }, [archives]);
  const items = mergeArtifacts(artifacts, archiveArtifacts);
  // 消息内联默认产物 = OpenFilm film 预览（播放、无 timeline）；右侧侧边栏才是编辑视角（studio，带 timeline）。
  // 仅当用户要导出/发布出 mp4 时，创作产物才改显示 mp4。
  const isFilm = (a: Artifact) => a.kind === 'html' && /film\.html$/i.test(a.name);
  const nonFilm = isVideoSession ? items.filter((a) => !isFilm(a)) : items;
  const hasMp4 = nonFilm.some((a) => a.kind === 'video');
  const showStudio = isVideoSession && !hasMp4 && (videoProduct || (items.length > 0 && nonFilm.length === 0));
  if (showStudio) return <StudioPreviewGallery sessionKey={sessionKey} persona={persona} />;
  const displayItems = isVideoSession ? nonFilm : items;
  const current = Math.min(index, displayItems.length - 1);
  const artifact = displayItems[current];
  if (!artifact) return null;
  const move = (direction: number) => setIndex((current + direction + displayItems.length) % displayItems.length);
  return (
    <section className="artifact-gallery" aria-label="对话产物预览" aria-roledescription="轮播"
      tabIndex={displayItems.length > 1 ? 0 : undefined}
      onKeyDown={event => {
        if (event.target !== event.currentTarget || displayItems.length < 2) return;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault();
          move(event.key === 'ArrowLeft' ? -1 : 1);
        }
      }}>
      <div className="artifact-header"><IconOutputs size={16} /><span>创作产物</span>
        <span className="artifact-count" aria-live="polite">{current + 1} / {displayItems.length}</span></div>
      <div className="artifact-stage">
        <ArtifactPreview key={artifact.url} artifact={artifact} />
        {displayItems.length > 1 && <>
          <button type="button" className="artifact-arrow previous" aria-label="上一个产物" onClick={() => move(-1)}>
            <IconChevron size={20} /></button>
          <button type="button" className="artifact-arrow next" aria-label="下一个产物" onClick={() => move(1)}>
            <IconChevron size={20} /></button>
        </>}
      </div>
      <div className="artifact-footer">
        <span className="artifact-name" title={artifact.name}>{artifact.name}</span>
        <a href={artifact.url} target="_blank" rel="noreferrer">打开 ↗</a>
        <a href={artifact.url} download={artifact.name}>下载</a>
      </div>
    </section>
  );
}
