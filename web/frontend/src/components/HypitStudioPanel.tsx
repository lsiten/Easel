import { useCallback, useEffect, useRef, useState } from 'react';
import { handshakeHypitStudio, type HypitScope } from '../lib/api';
import { useHypitStudio } from '../lib/useHypitStudio';
import { IconExternal, IconRefresh, IconVideo } from './icons';

export default function HypitStudioPanel({ scope, runId }: { scope: HypitScope; runId?: string }) {
  const { studio, loading, error, refresh, control } = useHypitStudio(scope, runId, 6000);
  const frame = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const expectedOrigin = studio?.studio ? new URL(studio.studio, window.location.origin).origin : '';

  useEffect(() => {
    setReady(false);
    const onMessage = (event: MessageEvent) => {
      if (!studio?.studio || !frame.current?.contentWindow || event.source !== frame.current.contentWindow || event.origin !== expectedOrigin) return;
      const data = event.data;
      if (!data || typeof data !== 'object' || data.source !== 'hypit-studio' || data.engine !== 'hypit' || data.type !== 'hypit:ready') return;
      if (data.nonce !== studio.nonce || data.run_id !== studio.run_id || data.session_id !== scope.session_id || data.profile_id !== scope.profile_id) return;
      if (!studio.token || !studio.nonce || !studio.run_id) return;
      void handshakeHypitStudio(scope, { run_id: studio.run_id, token: studio.token, nonce: studio.nonce })
        .then(() => setReady(true)).catch(() => setReady(false));
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [expectedOrigin, scope.profile_id, scope.session_id, studio?.nonce, studio?.profile_id, studio?.run_id, studio?.studio, studio?.token, scope]);

  const run = useCallback((action: 'open' | 'refresh' | 'cancel') => { void control(action).catch(() => refresh()); }, [control, refresh]);
  const fallback = studio?.external_url || '';

  return <aside className="video-studio-panel hypit-studio-panel">
    <div className="vsp-head">
      <span className="vsp-title"><IconVideo size={16} /> Hypit Studio</span>
      <div className="vsp-head-actions">
        <button className="btn btn-sm" onClick={() => run('refresh')} title="刷新"><IconRefresh size={13} /> 刷新</button>
        {fallback && <a className="btn btn-sm" href={fallback} target="_blank" rel="noreferrer"><IconExternal size={13} /> 独立窗口</a>}
      </div>
    </div>
    <div className="vsp-body">
      {loading ? <div className="vsp-empty">检测 Hypit Studio…</div> : error ? <div className="vsp-empty">{error}</div> : studio?.studio ? <iframe
        ref={frame} key={`${studio.studio}:${studio.expires_at || ''}`} className="vsp-frame" src={studio.studio} title="Hypit Studio"
        sandbox="allow-scripts allow-same-origin allow-forms" /> : <div className="vsp-empty"><div>Hypit Studio 暂不可用</div>{fallback && <a href={fallback} target="_blank" rel="noreferrer" className="btn btn-sm">在独立窗口打开</a>}</div>}
    </div>
    {studio?.studio && <div className="vsp-foot"><span style={{ color: ready ? 'var(--green)' : 'var(--text-secondary)', fontSize: 12 }}>{ready ? '连接已验证' : '等待 Studio 握手…'}</span><button className="btn btn-sm" onClick={() => run('cancel')}>取消运行</button><button className="btn btn-sm" onClick={() => run('open')}>打开</button></div>}
  </aside>;
}
