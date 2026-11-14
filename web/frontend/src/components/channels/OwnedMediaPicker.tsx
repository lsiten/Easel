import { useEffect, useState } from 'react';
import { fetchOutputs } from '../../lib/api';
import type { OutputNode } from '../../lib/api';
import { channelError } from '../../lib/channelsApi';

export function OwnedMediaPicker({ persona, value, onChange, disabled = false, kind = '' }: {
  readonly persona: string; readonly value: string; readonly onChange: (path: string) => void; readonly disabled?: boolean; readonly kind?: string;
}) {
  const [files, setFiles] = useState<readonly OutputNode[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    fetchOutputs(persona, controller.signal).then((roots) => {
      if (controller.signal.aborted) return;
      const result: OutputNode[] = [];
      function walk(item: OutputNode) {
        if (item.external || item.assets) return;
        if (item.type === 'file' && ['image', 'video', 'audio'].includes(item.kind || '') && (!kind || item.kind === kind)) result.push(item);
        item.children?.forEach(walk);
      }
      roots.forEach(walk); setFiles(result);
    }).catch((failure: unknown) => { if (!controller.signal.aborted) setError(channelError(failure)); });
    return () => controller.abort();
  }, [persona, kind]);
  return <><select className="field" aria-label="选择当前画像媒体文件" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
    <option value="">选择素材源媒体</option>
    {value && !files.some((file) => file.path === value) && <option value={value}>{value}</option>}
    {files.map((file) => <option key={file.path} value={file.path}>{file.name} · {file.path}</option>)}
  </select>{error && <p role="alert">{error}</p>}</>;
}
