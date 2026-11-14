import { useState } from 'react';
import type { PlatformConnection } from '../../lib/platformConnections';
import { preferredConnection } from '../../lib/platformConnections';
import { BrowserChannelWorkspace } from './BrowserChannelWorkspace';
import { ChannelAccounts } from './ChannelAccounts';
import { ChannelApplicationSetup } from './ChannelApplicationSetup';

export type ConfigureApplication = (platform: string, signal: AbortSignal) => Promise<void>;

export function PlatformConnectionWorkspace({ persona, connection, onConfigureApplication, onRefresh }: {
  readonly persona: string; readonly connection: PlatformConnection;
  readonly onConfigureApplication: ConfigureApplication; readonly onRefresh: () => void;
}) {
  const [method, setMethod] = useState(preferredConnection(connection));
  const [browserConnected, setBrowserConnected] = useState(connection.browser?.loggedIn === true);
  return <>
    <div className="subnav-tabs" role="group" aria-label="平台接入方式" style={{ flexWrap: 'wrap', marginBlock: 'var(--space-4, 16px)' }}>
      {connection.browser && <button className={`subnav-tab ${method === 'browser' ? 'active' : ''}`} aria-pressed={method === 'browser'}
        onClick={() => setMethod('browser')}>浏览器接入{browserConnected ? ' · 已登录' : ''}</button>}
      {connection.application && <button className={`subnav-tab ${method === 'application' ? 'active' : ''}`} aria-pressed={method === 'application'}
        onClick={() => setMethod('application')}>应用授权{connection.application.configured ? ' · 已配置' : ''}</button>}
    </div>
    {connection.application && <ChannelApplicationSetup persona={persona} spec={connection.application}
      onConfigureApplication={onConfigureApplication} onSaved={onRefresh} optional={method === 'browser'} />}
    {method === 'browser' && connection.browser && <BrowserChannelWorkspace key={`${persona}:${connection.browser.platform}`} persona={persona} account={connection.browser} onConnectionChange={setBrowserConnected} />}
    {method === 'application' && connection.application && <ChannelAccounts key={`${persona}:${connection.application.platform}`} persona={persona} spec={connection.application} />}
  </>;
}
