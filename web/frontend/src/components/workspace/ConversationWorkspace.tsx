import type { ReactNode } from 'react';
import type { Page } from '../../lib/navigation';
import { IconChat, IconSkills } from '../icons';

const TABS = [
  ['chat', '会话', IconChat],
  ['tasks', 'Agent 任务', IconSkills],
] as const;

export function ConversationWorkspace({ page, chat, tasks, onNavigate }: {
  readonly page: Page;
  readonly chat: ReactNode;
  readonly tasks: ReactNode;
  readonly onNavigate: (page: Page) => void;
}) {
  const active = page === 'tasks' ? 'tasks' : 'chat';
  return <div className="section-workspace">
    <nav className="section-nav" aria-label="对话">
      {TABS.map(([id, label, Icon]) => <button key={id}
        className={`section-nav-tab ${active === id ? 'active' : ''}`}
        aria-current={active === id ? 'page' : undefined}
        onClick={() => onNavigate(id)}><Icon size={15} />{label}</button>)}
    </nav>
    <div className={`section-body ${active === 'tasks' ? 'section-body-scroll' : ''}`}>
      {active === 'chat' ? chat : <div className="conversation-task-list">{tasks}</div>}
    </div>
  </div>;
}
