import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchHypitStudio, launchHypitStudio, type HypitScope, type HypitStudioInfo } from './api';

export interface HypitStudioState {
  readonly studio: HypitStudioInfo | null;
  readonly loading: boolean;
  readonly error: string;
  readonly refresh: () => void;
  readonly control: (action: 'open' | 'refresh' | 'cancel') => Promise<void>;
}

/** Poll a run-scoped Hypit Studio lease. The lease is replaced before controls are sent. */
export function useHypitStudio(scope: HypitScope, runId?: string, pollMs = 0, enabled = true): HypitStudioState {
  const scopeSession = scope.session_id;
  const scopeProfile = scope.profile_id;
  const requestScope = useMemo(() => ({ session_id: scopeSession, profile_id: scopeProfile }), [scopeSession, scopeProfile]);
  const key = JSON.stringify([scopeSession, scopeProfile, runId || null]);
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ key: string; studio: HypitStudioInfo | null; error: string } | null>(null);
  const refresh = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof window.setTimeout> | undefined;
    const load = async () => {
      try {
        const studio = await fetchHypitStudio(requestScope, controller.signal, runId);
        if (!controller.signal.aborted) setState({ key, studio, error: '' });
      } catch (error) {
        if (!controller.signal.aborted) setState({ key, studio: null, error: error instanceof Error ? error.message : '加载失败' });
      } finally {
        if (!controller.signal.aborted && pollMs > 0) timer = window.setTimeout(load, pollMs);
      }
    };
    void load();
    return () => { controller.abort(); if (timer !== undefined) window.clearTimeout(timer); };
  }, [key, requestScope, runId, pollMs, revision, enabled]);

  const current = enabled && state?.key === key ? state.studio : null;
  const control = useCallback(async (action: 'open' | 'refresh' | 'cancel') => {
    if (!runId) return;
    const lease = await fetchHypitStudio(requestScope, undefined, runId);
    if (!lease.available || !lease.token) throw new Error('Hypit Studio 不可用');
    await launchHypitStudio(requestScope, undefined, lease, action);
    refresh();
  }, [runId, requestScope, refresh]);

  return { studio: current, loading: enabled && !current, error: state?.key === key ? state.error : '', refresh, control };
}
