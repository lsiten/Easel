import { useEffect, useState } from 'react';
import { channelError, channelRequest, readChannelPlatforms } from '../lib/channelsApi';
import type { ChannelPlatform } from '../lib/channelsApi';
import { fetchAccounts } from '../lib/api';
import type { AccountItem } from '../lib/api';
import { platformConnections } from '../lib/platformConnections';
import { PlatformConnectionWorkspace } from './channels/PlatformConnectionWorkspace';
import type { ConfigureApplication } from './channels/PlatformConnectionWorkspace';

export default function ChannelsPage({ persona, onConfigureApplication }: { readonly persona: string; readonly onConfigureApplication: ConfigureApplication }) {
  const [platforms, setPlatforms] = useState<readonly ChannelPlatform[]>([]);
  const [browserAccounts, setBrowserAccounts] = useState<readonly AccountItem[]>([]);
  const [platform, setPlatform] = useState('');
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.allSettled([fetchAccounts(persona), channelRequest('/api/channels/platforms', { signal: controller.signal }).then(readChannelPlatforms)]).then(([browser, application]) => {
      if (controller.signal.aborted) return;
      const browserRows = browser.status === 'fulfilled' ? browser.value : [];
      const applicationRows = application.status === 'fulfilled' ? application.value : [];
      setBrowserAccounts(browserRows); setPlatforms(applicationRows);
      const choices = platformConnections(browserRows, applicationRows);
      setPlatform((current) => choices.some((item) => item.id === current) ? current : choices.find((item) => item.browser?.loggedIn)?.id || choices[0]?.id || '');
      setError([browser, application].filter((item) => item.status === 'rejected').map((item) => item.status === 'rejected' ? channelError(item.reason) : '').join('；'));
    }).catch((failure: unknown) => { if (!controller.signal.aborted) setError(channelError(failure)); });
    return () => controller.abort();
  }, [reload, persona]);
  const choices = platformConnections(browserAccounts, platforms);
  const selected = choices.find((item) => item.id === platform);
  return <div className="accounts-page platform-workbench">
    <h1 className="page-title">平台工作台</h1>
    {!persona ? <div className="panel">请先在侧栏选择画像，再连接平台账号。</div> : <>
      <label className="field-label" htmlFor="channel-platform">平台</label>
      <select id="channel-platform" className="field" value={platform} onChange={(event) => setPlatform(event.target.value)}>
        {!choices.length && <option value="">正在读取平台…</option>}
        {choices.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
      <button className="btn btn-sm" style={{ marginTop: 'var(--space-3, 12px)' }} onClick={() => setReload((value) => value + 1)}>刷新接入状态</button>
      {error && <div className="panel" role="alert">{error} <button className="btn btn-sm" onClick={() => setReload((value) => value + 1)}>重试</button></div>}
      {selected ? <PlatformConnectionWorkspace key={JSON.stringify([persona, platform])} persona={persona} connection={selected}
        onConfigureApplication={onConfigureApplication} onRefresh={() => setReload((value) => value + 1)} />
        : !error && <p role="status">正在读取平台能力…</p>}
    </>}
  </div>;
}
