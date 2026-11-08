import { useCallback, useEffect, useRef, useState } from 'react';
import { assetsOrganize, fetchAssetOrganization } from './api';
import type { AssetOrganizationJob } from './api';

/** The server owns a job; navigating away only detaches its progress view. */
export function useAssetOrganization(persona: string, onComplete: () => void | Promise<void>) {
  const [job, setJob] = useState<AssetOrganizationJob | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const seenDone = useRef('');
  const latestComplete = useRef(onComplete);
  latestComplete.current = onComplete;
  const activePersona = useRef(persona);
  activePersona.current = persona;

  const receive = useCallback((next: AssetOrganizationJob | null) => {
    if (next && next.profile !== activePersona.current) return;
    setJob(next);
    setError(next?.status === 'failed' ? next.error : '');
    if (next?.status === 'done' && seenDone.current !== next.id) {
      seenDone.current = next.id;
      void latestComplete.current();
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setJob(null);
    setError('');
    const poll = async () => {
      if (!persona) return;
      try {
        const next = await fetchAssetOrganization(persona, controller.signal);
        if (!controller.signal.aborted) receive(next);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '整理进度加载失败');
      }
    };
    void poll();
    const timer = window.setInterval(poll, 3000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [persona, receive]);

  const start = useCallback(async () => {
    setStarting(true);
    setError('');
    try {
      receive(await assetsOrganize(false, persona));
    } catch (cause) {
      if (activePersona.current === persona) setError(cause instanceof Error ? cause.message : '整理失败');
    } finally {
      if (activePersona.current === persona) setStarting(false);
    }
  }, [persona, receive]);

  return { job, error, start, busy: starting || (job?.profile === persona && job.status === 'running') };
}
