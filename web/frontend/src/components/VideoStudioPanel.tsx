import { useState, useEffect, useCallback, useRef } from 'react';
import { launchOpenfilmStudio } from '../lib/api';
import type { Reference } from '../lib/api';
import { normalizeOpenfilmRef } from '../lib/openfilmReferences';
import { useOpenfilmStudio } from '../lib/useOpenfilmStudio';
import { IconVideo, IconRefresh, IconExternal } from './icons';

export default function VideoStudioPanel({ onAddRef, session, persona }: {
  onAddRef?: (r: Reference) => void;
  session?: string;
  persona?: string;
}) {
  const { studio, loading, error: err, refresh: load } = useOpenfilmStudio({ session, persona }, 6000);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState('');
  const [refFlash, setRefFlash] = useState<Reference | null>(null);
  const autoLaunched = useRef(false);
  const launchGeneration = useRef(0);
  const frame = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    autoLaunched.current = false;
    setLaunching(false);
    setLaunchError('');
    setRefFlash(null);
    return () => { launchGeneration.current += 1; };
  }, [session, persona]);
  useEffect(() => {
    if (studio?.studio) setLaunchError('');
  }, [studio?.studio]);

  // 「像 Codex 一样引用网页里的元素」：OpenFilm Studio（跨源 iframe）把被引用元素
  // 经 postMessage 交给本面板（openfilm:reference），点一下即引用到对话，不靠固定清单。
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (!studio?.studio || !frame.current?.contentWindow || e.source !== frame.current.contentWindow
          || e.origin !== new URL(studio.studio, window.location.origin).origin) return;
      const d = e.data;
      if (!d || typeof d !== 'object') return;
      if (d.source !== 'openfilm-studio' || d.type !== 'openfilm:reference') return;
      const ref = normalizeOpenfilmRef(d.ref, { session, persona, project: studio.topic || studio.studio });
      if (ref) { onAddRef?.(ref); setRefFlash(ref); }   // 收到引用 → 高亮反馈，明确「点元素即引用」生效
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [onAddRef, studio?.studio, studio?.topic, session, persona]);
  // 引用到达时短暂高亮面板 + 提示条，让用户明确知道「在 Studio 里点元素」已引用到对话。
  useEffect(() => {
    if (!refFlash) return;
    const t = setTimeout(() => setRefFlash(null), 2200);
    return () => clearTimeout(t);
  }, [refFlash]);

  const launch = useCallback(async () => {
    if (launching) return;
    const generation = launchGeneration.current;
    setLaunching(true);
    setLaunchError('');
    try {
      await launchOpenfilmStudio(undefined, session, persona);
    } catch (error) {
      /* 启动失败：保持轮询，用户可再点「启动」重试 */
      if (generation === launchGeneration.current) {
        setLaunchError(error instanceof Error ? error.message : 'Studio 启动失败，请稍后重试。');
      }
    } finally {
      if (generation === launchGeneration.current) setLaunching(false);
    }
  }, [launching, session, persona]);

  // 轮询：OpenFilm Studio 在制作过程中会启动/更新地址，定时刷新即可看到最新可视化。
  // 若 OpenFilm 能力就绪但 Studio 尚未就绪（对话里正在做视频、Agent 尚未拉起 Studio），
  // 自动启动一次（自愈），并显示「启动 Studio」按钮供手动重试。
  useEffect(() => {
    if (!studio) return;
    if (studio.studio) return; // 已就绪，无需启动
    if (!studio.available) return; // 能力未就绪，启动无意义
    if (autoLaunched.current) return;
    autoLaunched.current = true;
    void launch();
  }, [studio, launch]);

  const canLaunch = !studio?.studio && !!studio?.available;

  return (
    <aside className={`video-studio-panel${refFlash ? ' refer-pulse' : ''}`}>
      <div className="vsp-head">
        <span className="vsp-title"><IconVideo size={16} /> OpenFilm 制作过程</span>
        <div className="vsp-head-actions">
          {studio?.studio && (
            <button className="btn btn-sm"
              onClick={() => window.open(studio.studio, '_blank', 'noopener')}
              title="在独立窗口打开（本项目视频预览，按项目隔离、不串台）">
              <IconExternal size={13} /> 独立窗口
            </button>
          )}
          <button className="btn btn-sm" onClick={() => { setLaunchError(''); load(); }} title="刷新"><IconRefresh size={13} /> 刷新</button>
        </div>
      </div>
      {/* 醒目引用提示：明确「点 Studio 里的元素即引用」，让 Agent 可据此理解并操作 */}
      <div className="vsp-refer-hint">
        <span className="vsp-refer-hint-icon" aria-hidden><IconVideo size={14} /></span>
        <span>点 Studio 里的 <b>镜头 / 元素</b> 即引用到对话 —— Agent 可据此理解并操作</span>
      </div>
      {refFlash && (
        <div className="vsp-refer-toast" role="status">
          <span className="vsp-refer-toast-dot" aria-hidden /> 已引用「{refFlash.label}」到对话
        </div>
      )}
      <div className="vsp-body">
        {loading && !studio ? <div className="vsp-empty vsp-loading" role="status">
          <span className="spinner" aria-hidden />检测 OpenFilm Studio…
        </div>
          : (err || launchError) ? <div className="vsp-empty" role="alert">
            <div>无法打开制作过程</div>
            <div className="vsp-error">{launchError || err}</div>
            <button className="btn btn-sm" onClick={() => { setLaunchError(''); load(); }}>重试连接</button>
          </div>
          : studio?.studio ? (
            // key=rev：film 预览 URL 稳定，rev 变=Agent 改了 film.html→重载 iframe 实时反映制作过程；
            // rev 不变（未编辑）则不重载，不打断当前预览/播放。
            <iframe ref={frame} key={`${studio.studio}:${studio.rev || ''}`} className="vsp-frame" src={studio.studio} title="OpenFilm Studio"
              sandbox="allow-scripts allow-same-origin allow-popups" />
          ) : (
            <div className="vsp-empty">
              <div>OpenFilm 正在准备可视化…</div>
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                {studio?.available
                  ? (launching ? '正在启动 Studio，稍候自动刷新…' : 'Studio 启动中，稍候自动刷新，或点「启动」重试。')
                  : 'OpenFilm 未就绪，运行 easel setup 安装视频制作套件。'}
              </div>
              {canLaunch && (
                <button className="btn btn-sm" onClick={launch}
                  disabled={launching} style={{ marginTop: 4, alignSelf: 'center' }}>
                  {launching ? '启动中…' : '启动 Studio'}
                </button>
              )}
            </div>
          )}
      </div>
      {studio?.studio && (
        <div className="vsp-foot">
          <a href={studio.studio} target="_blank" rel="noreferrer"
            className="btn btn-sm" style={{ flex: 1, textAlign: 'center' }}
            title="在独立窗口打开本项目视频 Studio（编辑 / 制作视角，按项目隔离、不串台）">
            打开视频studio ↗
          </a>
        </div>
      )}
    </aside>
  );
}
