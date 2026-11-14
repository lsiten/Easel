import { useState } from 'react';
import type { ChannelPlatform, ChannelScope } from '../../lib/channelsApi';
import { ChannelPublish } from './ChannelPublish';
import { ChannelData } from './ChannelData';
import { ChannelEngagement } from './ChannelEngagement';
import { ChannelContent } from './ChannelContent';
import { ChannelTasks } from './ChannelTasks';
import { ChannelSettings } from '../ChannelSettings';
import { ChannelNativeHandoff } from './ChannelNativeHandoff';

const TABS = [
  ['publish', '发布'], ['data', '作品数据'], ['engagement', '互动'],
  ['content', '内容'], ['tasks', '任务记录'], ['settings', '设置'],
] as const;
type Tab = typeof TABS[number][0];

export function ChannelWorkspace({ scope, spec }: { readonly scope: ChannelScope; readonly spec: ChannelPlatform }) {
  const [tab, setTab] = useState<Tab>('publish');
  const handoff = ['douyin-open', 'rednote-link'].includes(spec.platform);
  function renderWorkspace() {
    const accountFree = (tab === 'publish' && handoff) || (tab === 'data' && spec.account_binding === 'handoff');
    if (!scope.account_uid && ['publish', 'data', 'engagement'].includes(tab) && !accountFree) {
      return <div className="panel">{spec.auth_mode === 'app_handoff' ? '配置原生应用账号后使用作品读取功能。' : '连接并选择平台账号后使用此功能。'}</div>;
    }
    switch (tab) {
      case 'publish':
        if (handoff) return <ChannelNativeHandoff persona={scope.persona} spec={spec} />;
        if (!spec.publish_modes.length) return <div className="panel">当前平台支持账号授权与企业身份读取。</div>;
        return <ChannelPublish scope={scope} spec={spec} />;
      case 'data': return <ChannelData scope={scope} spec={spec} />;
      case 'engagement': return spec.engagement ? <ChannelEngagement scope={scope} platform={spec.platform} /> : <div className="panel">当前平台接入能力未开放评论互动。</div>;
      case 'content': return <ChannelContent persona={scope.persona} />;
      case 'tasks': return <ChannelTasks scope={scope} platform={spec.platform} />;
      case 'settings': return <ChannelSettings persona={scope.persona} />;
    }
  }
  return <>
    <div className="subnav-tabs" role="group" aria-label="平台工作区" style={{ flexWrap: 'wrap', marginBottom: 'var(--space-4, 16px)' }}>
      {TABS.map(([id, label]) => <button key={id} className={`subnav-tab ${tab === id ? 'active' : ''}`}
        aria-pressed={tab === id} onClick={() => setTab(id)}>{label}</button>)}
    </div>
    {renderWorkspace()}
  </>;
}
