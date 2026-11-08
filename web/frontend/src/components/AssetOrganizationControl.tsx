import { useAssetOrganization } from '../lib/useAssetOrganization';
import { IconRefresh } from './icons';

/** Content-library entry point shares the same persistent job as the asset page. */
export function AssetOrganizationControl({ persona, onComplete }: {
  readonly persona: string;
  readonly onComplete: () => void | Promise<void>;
}) {
  const { job, error, start, busy } = useAssetOrganization(persona, onComplete);
  return <>
    <button className="btn btn-sm" onClick={start} disabled={busy || !persona} style={{ minWidth: 138 }}>
      <IconRefresh size={14} /> {busy ? '正在理解素材…' : '整理素材'}
    </button>
    {busy && job && <span role="status">{job.completed} / {job.total} 项</span>}
    {error && <span role="alert">{error}</span>}
  </>;
}
