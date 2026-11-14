import { useCallback, useState } from 'react';
import AccountsPage from '../AccountsPage';
import PublishPage from '../PublishPage';
import { PlatformPublishingPanel } from '../workspace/PlatformPublishingPanel';
import type { AccountItem } from '../../lib/api';
import { BrowserChannelData } from './BrowserChannelData';
import { ChannelTasks } from './ChannelTasks';

const TABS = [['publish', '发布'], ['data', '作品数据'], ['tasks', '任务记录']] as const;
type BrowserTab = typeof TABS[number][0];

export function BrowserChannelWorkspace({ persona, account, onConnectionChange }: {
  readonly persona: string; readonly account: AccountItem; readonly onConnectionChange: (connected: boolean) => void;
}) {
  const [connected, setConnected] = useState(account.loggedIn);
  const [tab, setTab] = useState<BrowserTab>('publish');
  const changed = useCallback((value: AccountItem | null) => {
    const ready = value?.loggedIn === true;
    setConnected(ready); onConnectionChange(ready);
  }, [onConnectionChange]);
  function workspace() {
    if (tab === 'publish') return <PlatformPublishingPanel persona={persona} platform={account.platform} automaticLabel="浏览器自动发布"
      automatic={connected ? <PublishPage persona={persona} platform={account.platform} embedded />
        : <p className="panel">扫码登录当前平台后即可使用浏览器自动发布；人工审核发布不受登录状态限制。</p>} />;
    if (!connected && tab === 'data') return <p className="panel">扫码登录当前平台后即可使用浏览器接入。</p>;
    switch (tab) {
      case 'data': return <BrowserChannelData persona={persona} platform={account.platform} />;
      case 'tasks': return <ChannelTasks platform={account.platform} scope={{ persona, account_uid: '' }} />;
    }
  }
  return <>
    <AccountsPage persona={persona} platform={account.platform} embedded onAccountChange={changed} />
    <div className="subnav-tabs" role="group" aria-label="浏览器平台工作区" style={{ flexWrap: 'wrap', marginBlock: 'var(--space-4, 16px)' }}>
      {TABS.map(([id, label]) => <button key={id} className={`subnav-tab ${tab === id ? 'active' : ''}`} aria-pressed={tab === id}
        onClick={() => setTab(id)}>{label}</button>)}
    </div>
    {workspace()}
  </>;
}
