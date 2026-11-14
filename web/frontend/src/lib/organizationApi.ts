import { request } from './api';

export type OrganizationJobStatus = 'queued' | 'running' | 'done' | 'failed' | 'rolled_back';

export type ProjectOrganizationJob = {
  readonly id: string;
  readonly profile: string;
  readonly projects: readonly string[];
  readonly status: OrganizationJobStatus;
  readonly total: number;
  readonly completed: number;
  readonly moved: number;
  readonly reused: number;
  readonly resume_from: number;
  readonly current_project: string;
  readonly rollback_available: boolean;
  readonly transactions: readonly string[];
  readonly error: string;
  readonly updated: number;
};

export type AssetEntry = {
  readonly path: string;
  readonly name: string;
  readonly kind: string;
  readonly category: string;
  readonly tags: readonly string[];
  readonly summary: string;
  readonly source: string;
  readonly capturedAt?: string;
  readonly location?: string;
  readonly timeSource?: string;
  readonly project?: string;
  readonly role?: string;
  readonly confidence?: number;
  readonly reason?: string;
  readonly manual?: boolean;
};

export type AssetEntryUpdate = {
  readonly path: string;
  readonly category?: string;
  readonly tags?: readonly string[];
  readonly summary?: string;
  readonly capturedAt?: string;
  readonly location?: string;
  readonly project?: string;
  readonly role?: string;
};

const scoped = (path: string, persona: string): string =>
  `${path}${path.includes('?') ? '&' : '?'}persona=${encodeURIComponent(persona)}`;

export function organizeProjects(persona: string, project = ''): Promise<ProjectOrganizationJob> {
  return request(scoped('/api/projects/organize', persona), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project }),
  });
}

export function fetchProjectOrganization(
  persona: string,
  signal?: AbortSignal,
): Promise<ProjectOrganizationJob | null> {
  return request(scoped('/api/projects/organize', persona), { signal });
}

export function rollbackProjectOrganization(
  persona: string,
  identifier: string,
): Promise<ProjectOrganizationJob> {
  return request(scoped(
    `/api/projects/organize/${encodeURIComponent(identifier)}/rollback`,
    persona,
  ), { method: 'POST' });
}

export function fetchAssetEntry(path: string, persona: string): Promise<AssetEntry> {
  return request(scoped(`/api/assets/entry?path=${encodeURIComponent(path)}`, persona));
}

export function updateAssetEntry(persona: string, update: AssetEntryUpdate): Promise<AssetEntry> {
  return request(scoped('/api/assets/entry', persona), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  });
}
