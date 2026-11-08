import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { fetchOpenfilmStudio } from './api';
import type { OpenfilmStudioInfo } from './api';

interface StudioState {
  readonly scope: string;
  readonly studio: OpenfilmStudioInfo | null;
  readonly error: string;
  readonly loading: boolean;
  readonly refresh: () => void;
}

export const OpenfilmStudioContext = createContext<StudioState | null>(null);

/** Studio state belongs to a session/persona pair, including while a new request is pending. */
export function useOpenfilmStudio(context: {
  readonly session: string | undefined;
  readonly persona: string | undefined;
  readonly enabled?: boolean;
}, pollMs = 0) {
  const { session, persona, enabled = true } = context;
  const scope = JSON.stringify([session || null, persona || null]);
  const shared = useContext(OpenfilmStudioContext);
  const inherits = enabled && shared?.scope === scope;
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{
    readonly scope: string;
    readonly studio: OpenfilmStudioInfo | null;
    readonly error: string;
  } | null>(null);
  const refresh = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    if (!enabled || inherits) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof window.setTimeout> | undefined;
    const load = async () => {
      try {
        const studio = await fetchOpenfilmStudio(session, persona, controller.signal);
        if (!controller.signal.aborted) setState({ scope, studio, error: '' });
      } catch (error) {
        if (!controller.signal.aborted) {
          setState({ scope, studio: null, error: error instanceof Error ? error.message : '加载失败' });
        }
      } finally {
        if (!controller.signal.aborted && pollMs > 0) timer = window.setTimeout(load, pollMs);
      }
    };
    void load();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [session, persona, scope, pollMs, revision, enabled, inherits]);

  if (state && (state.scope !== scope || !enabled)) setState(null);
  if (inherits) return shared;
  const current = enabled && state?.scope === scope ? state : null;
  return { scope, studio: current?.studio ?? null, error: current?.error ?? '', loading: enabled && !current, refresh };
}
