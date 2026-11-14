import { useCallback, useEffect, useState } from 'react';
import type { DirListing } from '../lib/api';
import { IconChevron, IconFolder } from './icons';

interface Props {
  readonly title: string;
  readonly initialPath?: string;
  readonly load: (path?: string) => Promise<DirListing>;
  readonly onClose: () => void;
  readonly onPick: (path: string) => void;
}

/** Local-directory browser shared by asset sources and environment tools. */
export function DirectoryPickerModal({
  title,
  initialPath = '',
  load,
  onClose,
  onPick,
}: Props) {
  const [listing, setListing] = useState<DirListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [draftPath, setDraftPath] = useState(initialPath);

  const navigate = useCallback((path?: string) => {
    setLoading(true);
    setError('');
    load(path)
      .then((next) => {
        setListing(next);
        setDraftPath(next.path);
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : '读取失败');
      })
      .finally(() => setLoading(false));
  }, [load]);

  useEffect(() => { navigate(initialPath); }, [initialPath, navigate]);

  const canGoUp = listing !== null && listing.parent !== listing.path;
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal dir-picker" role="dialog" aria-modal="true" aria-label={title}
        onClick={(event) => event.stopPropagation()}>
        <div className="dir-picker-head">
          <strong style={{ fontSize: 15 }}><IconFolder size={16} /> {title}</strong>
          <button className="btn btn-sm" onClick={onClose}>关闭</button>
        </div>
        <div className="dir-picker-path">
          <input
            className="field"
            value={draftPath}
            onChange={(event) => setDraftPath(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') navigate(draftPath); }}
            aria-label="目录路径"
          />
          <button className="btn btn-sm" onClick={() => navigate(draftPath)} disabled={loading}>前往</button>
        </div>
        <div className="dir-picker-body">
          {loading ? <div className="dash-empty">加载中…</div>
            : error ? <div className="notice-error" style={{ background: 'var(--surface-hover)' }}>{error}</div>
            : (listing && listing.dirs.length === 0) ? <div className="dash-empty">此目录下没有子文件夹，可直接「选择此目录」。</div>
            : (
              <div className="dir-picker-grid">
                {listing?.dirs.map((directory) => (
                  <div key={directory.path} className="card card-hover dir-picker-item" onClick={() => navigate(directory.path)}>
                    <IconFolder size={18} />
                    <span className="dir-picker-name">{directory.name}</span>
                    <IconChevron size={15} className="dir-picker-chevron" />
                  </div>
                ))}
              </div>
            )}
        </div>
        {listing?.quick.length ? (
          <div className="dir-picker-quick">
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>常用</span>
            {listing.quick.map((directory) => (
              <button key={directory.path} className="btn btn-sm" onClick={() => navigate(directory.path)}>
                {directory.name}
              </button>
            ))}
          </div>
        ) : null}
        <div className="dir-picker-foot">
          <button className="btn btn-sm" onClick={() => { if (listing) navigate(listing.parent); }} disabled={loading || !canGoUp}>
            返回上级
          </button>
          <button className="btn btn-sm btn-primary" onClick={() => onPick(listing?.path || '')} disabled={loading || !listing}>
            选择此目录
          </button>
        </div>
      </div>
    </div>
  );
}
