import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchProjectOrganization,
  organizeProjects,
  rollbackProjectOrganization,
} from './organizationApi';
import type { ProjectOrganizationJob } from './organizationApi';

export function useProjectOrganization(persona: string, onComplete: () => void | Promise<void>) {
  const [job, setJob] = useState<ProjectOrganizationJob | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const seenDone = useRef('');
  const activePersona = useRef(persona);
  const latestComplete = useRef(onComplete);
  activePersona.current = persona;
  latestComplete.current = onComplete;

  const receive = useCallback((next: ProjectOrganizationJob | null) => {
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
        const next = await fetchProjectOrganization(persona, controller.signal);
        if (!controller.signal.aborted) receive(next);
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : '整理进度加载失败');
        }
      }
    };
    void poll();
    const timer = window.setInterval(poll, 3000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [persona, receive]);

  const start = useCallback(async (project = '') => {
    setStarting(true);
    setError('');
    try {
      receive(await organizeProjects(persona, project));
    } catch (cause) {
      if (activePersona.current === persona) {
        setError(cause instanceof Error ? cause.message : 'AI 整理失败');
      }
    } finally {
      if (activePersona.current === persona) setStarting(false);
    }
  }, [persona, receive]);

  const rollback = useCallback(async () => {
    if (!job?.rollback_available) return;
    setStarting(true);
    setError('');
    try {
      receive(await rollbackProjectOrganization(persona, job.id));
      await latestComplete.current();
    } catch (cause) {
      if (activePersona.current === persona) {
        setError(cause instanceof Error ? cause.message : '撤销整理失败');
      }
    } finally {
      if (activePersona.current === persona) setStarting(false);
    }
  }, [persona, job, receive]);

  return {
    job,
    error,
    start,
    rollback,
    busy: starting || (job?.profile === persona && ['queued', 'running'].includes(job.status)),
  };
}
