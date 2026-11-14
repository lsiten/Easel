import { useState } from 'react';
import { AiConfiguration } from '../ai/AiConfiguration';
import { ChannelSettings } from '../ChannelSettings';
import { IconAccounts } from '../icons';
import { IconGlobe, IconSlidersHorizontal } from '../settingsIcons';
import { ProxySettings } from './ProxySettings';

const TABS = [
  ['ai', '运行高级参数', IconSlidersHorizontal],
  ['proxy', '网络代理', IconGlobe],
  ['platform', '平台与 API 接入', IconAccounts],
] as const;

export function SettingsMore({ persona }: {
  readonly persona: string;
}) {
  const [tab, setTab] = useState<typeof TABS[number][0]>('ai');
  return <div className="settings-more">
    <nav className="section-nav section-nav-inline" aria-label="更多设置">
      {TABS.map(([id, label, Icon]) => <button type="button" key={id}
        className={`section-nav-tab ${tab === id ? 'active' : ''}`}
        aria-current={tab === id ? 'page' : undefined}
        onClick={() => setTab(id)}><Icon size={15} />{label}</button>)}
    </nav>
    <div className="settings-more-body">
      {tab === 'ai' ? <AiConfiguration /> : tab === 'proxy' ? <ProxySettings /> : <>
        {persona ? <ChannelSettings persona={persona} />
          : <div className="panel">请先在侧栏选择画像，再管理画像密钥与平台接入设置。</div>}
      </>}
    </div>
  </div>;
}
