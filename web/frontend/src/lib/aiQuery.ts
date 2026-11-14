import { aiRequest } from './aiWorkspaceApi';

export async function loadAiQuery<T>(path: string, signal: AbortSignal, parse: (value: unknown) => T): Promise<T | null> {
  const value = await aiRequest(path, signal);
  return signal.aborted ? null : parse(value);
}
