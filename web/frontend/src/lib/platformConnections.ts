import type { AccountItem } from './api';
import type { ChannelPlatform } from './channelsApi';

export type PlatformConnection = {
  readonly id: string;
  readonly name: string;
  readonly browser: AccountItem | null;
  readonly application: ChannelPlatform | null;
};

const BROWSER_IDS: Readonly<Record<string, string>> = {
  'rednote-link': 'xiaohongshu', 'douyin-open': 'douyin', 'kwai-open': 'kuaishou',
  'bilibili-open': 'bilibili', 'wechat-official-open': 'wechat-oa', 'wechat-channels-open': 'weixin-channels',
};

export function browserPlatformFor(applicationPlatform: string): string {
  return BROWSER_IDS[applicationPlatform] || applicationPlatform;
}

export function platformConnections(browser: readonly AccountItem[], applications: readonly ChannelPlatform[]): readonly PlatformConnection[] {
  const rows = browser.filter((item) => item.supported).map((item): PlatformConnection => ({
    id: item.platform, name: item.name, browser: item, application: null,
  }));
  for (const application of applications) {
    const id = browserPlatformFor(application.platform);
    const index = rows.findIndex((item) => item.id === id);
    if (index >= 0) rows[index] = { ...rows[index], application };
    else rows.push({ id, name: application.display_name, browser: null, application });
  }
  return rows;
}

export function preferredConnection(row: PlatformConnection): 'browser' | 'application' {
  return row.browser?.loggedIn || !row.application ? 'browser' : 'application';
}
