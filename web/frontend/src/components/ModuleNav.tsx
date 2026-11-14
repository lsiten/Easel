import type { ComponentType } from 'react';
import {
  IconChat, IconCompass, IconDashboard, IconLayers, IconOutputs,
  IconProfile, IconPublish,
} from './icons';
import type { Page } from '../lib/navigation';
import { contentSectionFor } from '../lib/navigation';

type NavItem = {
  readonly page: Page;
  readonly label: string;
  readonly Icon: ComponentType<{ size?: number }>;
};

const WORKSPACE_NAV = [
  { page: 'dashboard', label: '总览', Icon: IconDashboard },
  { page: 'chat', label: '对话', Icon: IconChat },
  { page: 'trends', label: '策划', Icon: IconCompass },
  { page: 'create', label: '创作', Icon: IconLayers },
  { page: 'profile', label: '画像', Icon: IconProfile },
  { page: 'platform', label: '平台', Icon: IconPublish },
] as const satisfies readonly NavItem[];

const PLANNING_PAGES = new Set<Page>(['trends', 'ideas', 'calendar', 'breakdown']);
const CREATION_PAGES = new Set<Page>(['create', 'create-drafts', 'create-memory', 'create-video']);

function workspaceTarget(page: Page): Page {
  if (page === 'chat' || page === 'tasks') return 'chat';
  if (PLANNING_PAGES.has(page)) return 'trends';
  if (CREATION_PAGES.has(page)) return 'create';
  if (page === 'profile') return 'profile';
  return page;
}
export function WorkspaceNav({ current, onNavigate }: {
  readonly current: Page;
  readonly onNavigate: (page: Page) => void;
}) {
  const active = workspaceTarget(current);
  return <nav className="module-nav" aria-label="工作台">
    <span className="module-nav-title">工作台</span>
    <div className="module-nav-tabs">
      {WORKSPACE_NAV.map(({ page, label, Icon }) => <button key={page}
        className={`module-nav-tab ${active === page ? 'active' : ''}`}
        aria-current={active === page ? 'page' : undefined}
        onClick={() => onNavigate(page)}><Icon size={15} />{label}</button>)}
    </div>
  </nav>;
}

export function ContentNav({ current, onNavigate }: {
  readonly current: Page;
  readonly onNavigate: (page: Page) => void;
}) {
  const section = contentSectionFor(current) ?? 'projects';
  const items = [
    { section: 'projects', page: 'outputs', label: '项目与成品', Icon: IconOutputs },
    { section: 'assets', page: 'assets', label: '素材源', Icon: IconLayers },
    { section: 'drafts', page: 'content-drafts', label: '草稿文案', Icon: IconCompass },
    { section: 'generated', page: 'generated-history', label: '生成记录', Icon: IconLayers },
  ] as const;
  return <nav className="module-nav" aria-label="内容">
    <span className="module-nav-title">内容</span>
    <div className="module-nav-tabs">
      {items.map(({ section: itemSection, page, label, Icon }) => <button key={itemSection}
        className={`module-nav-tab ${section === itemSection ? 'active' : ''}`}
        aria-current={section === itemSection ? 'page' : undefined}
        onClick={() => onNavigate(page)}><Icon size={15} />{label}</button>)}
    </div>
  </nav>;
}
