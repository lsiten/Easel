import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { fetchPublishingPolicy } from '../../lib/api';
import { automaticPublishingAllowed, resolvePublishingMode } from '../../lib/publishingPolicy';
import type { PublishingMode } from '../../lib/publishingPolicy';
import ManualPublishPage from '../ManualPublishPage';

type PlatformPublishingPanelProps = {
  readonly persona: string;
  readonly platform: string;
  readonly automaticLabel: string;
  readonly automatic: ReactNode;
};

export function PlatformPublishingPanel({ persona, platform, automaticLabel, automatic }: PlatformPublishingPanelProps) {
  const [requested, setRequested] = useState<PublishingMode>('automated');
  const [policy, setPolicy] = useState<Awaited<ReturnType<typeof fetchPublishingPolicy>> | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    setError('');
    fetchPublishingPolicy().then((value) => {
      if (active) setPolicy(value);
    }).catch(() => {
      if (active) setError('无法读取发布方式，请重试。');
    });
    return () => { active = false; };
  }, [retry]);

  if (error) return <div className="panel" role="alert">{error}
    <button className="btn btn-sm" onClick={() => setRetry((value) => value + 1)}>重新读取</button>
  </div>;
  if (!policy) return <div className="dash-empty">正在读取发布方式…</div>;

  const allowed = automaticPublishingAllowed(policy);
  const mode = resolvePublishingMode(policy, requested);
  return <div className="platform-publishing-panel">
    <div className="publishing-mode-control">
      <label className="field-label" htmlFor="platform-publishing-mode">发布方式</label>
      <select id="platform-publishing-mode" className="field" value={mode}
        onChange={(event) => setRequested(event.target.value === 'automated' ? 'automated' : 'manual')}>
        <option value="automated" disabled={!allowed}>{automaticLabel}</option>
        <option value="manual">人工审核发布</option>
      </select>
      <span className="pv-hint">{!allowed
        ? '管理员已禁用自动提交，当前只开放人工审核与官方页面确认。'
        : mode === 'manual'
          ? '审核、复制或导出稿件后，到官方平台页面由你完成最终提交。'
          : '保留账号检查、内容预检、人工确认和平台状态核验。'}</span>
    </div>
    <div className="platform-publishing-content">
      {mode === 'manual' ? <ManualPublishPage persona={persona} platform={platform} /> : automatic}
    </div>
  </div>;
}
