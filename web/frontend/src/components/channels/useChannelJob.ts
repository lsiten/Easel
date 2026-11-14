import { useCallback, useEffect, useRef, useState } from 'react';
import { channelError } from '../../lib/channelsApi';

export function useChannelJob() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  useEffect(() => {
    controller.current = new AbortController();
    return () => { controller.current?.abort(); sequence.current += 1; };
  }, []);
  const perform = useCallback(async <T,>(operation: (signal: AbortSignal) => Promise<T>, accept: (value: T) => void) => {
    const active = controller.current;
    if (!active || active.signal.aborted) return;
    const request = ++sequence.current;
    setBusy(true); setError('');
    try {
      const value = await operation(active.signal);
      if (!active.signal.aborted && request === sequence.current) accept(value);
    } catch (failure: unknown) {
      if (!active.signal.aborted && request === sequence.current) setError(channelError(failure));
    } finally {
      if (!active.signal.aborted && request === sequence.current) setBusy(false);
    }
  }, []);
  return { busy, error, perform };
}
