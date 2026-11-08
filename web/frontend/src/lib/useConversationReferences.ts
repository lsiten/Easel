import { useCallback, useEffect, useRef, useState } from 'react';
import type { Reference } from './api';

/** Pending references are scoped just like the conversation that will consume them. */
export function useConversationReferences(session: string | undefined, persona: string | undefined) {
  const scope = JSON.stringify([session || null, persona || null]);
  const [state, setState] = useState<{ readonly scope: string; readonly refs: Reference[] }>({ scope, refs: [] });
  const refs = state.scope === scope ? state.refs : [];
  const snapshot = useRef({ scope, refs });
  snapshot.current = { scope, refs };
  useEffect(() => {
    setState({ scope, refs: [] });
  }, [scope]);
  const addRef = useCallback((ref: Reference) => {
    setState(previous => {
      const current = previous.scope === scope ? previous.refs : [];
      return { scope, refs: current.some(item => item.id === ref.id) ? current : [...current, ref] };
    });
  }, [scope]);
  const removeRef = useCallback((id: string) => {
    setState(previous => ({ scope, refs: previous.scope === scope ? previous.refs.filter(ref => ref.id !== id) : [] }));
  }, [scope]);
  const clearRefs = useCallback(() => {
    snapshot.current = { ...snapshot.current, refs: [] };
    setState(previous => ({ ...previous, refs: [] }));
  }, []);
  const refsFor = useCallback((targetSession: string | undefined, targetPersona: string | undefined) => {
    const targetScope = JSON.stringify([targetSession || null, targetPersona || null]);
    return snapshot.current.scope === targetScope ? snapshot.current.refs : [];
  }, []);
  return { refs, refsFor, addRef, removeRef, clearRefs };
}
