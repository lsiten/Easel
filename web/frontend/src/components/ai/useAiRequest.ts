import { useCallback, useEffect, useRef, useState } from 'react';
import { channelError } from '../../lib/channelsApi';

export function useAiRequest() {
  const mounted = useRef(false);
  const controllers = useRef(new Set<AbortController>());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { mounted.current = true; const activeControllers = controllers.current; return () => { mounted.current = false; for (const controller of activeControllers) controller.abort(); }; }, []);
  const run = useCallback(async <T,>(work: (signal: AbortSignal) => Promise<T>, cancelSignal?: AbortSignal): Promise<T | undefined> => {
    const controller = new AbortController();
    const cancel = () => controller.abort();
    cancelSignal?.addEventListener('abort', cancel, { once: true });
    if (cancelSignal?.aborted) controller.abort();
    controllers.current.add(controller); setBusy(true); setError('');
    try { return await work(controller.signal); }
    catch (failure: unknown) { if (!controller.signal.aborted) setError(channelError(failure)); return undefined; }
    finally { cancelSignal?.removeEventListener('abort', cancel); controllers.current.delete(controller); if (mounted.current) setBusy(controllers.current.size > 0); }
  }, []);
  return { run, busy, error };
}
