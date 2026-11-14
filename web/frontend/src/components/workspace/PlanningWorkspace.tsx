import type { Page } from '../../lib/navigation';
import TrendsPage from '../TrendsPage';
import IdeasPage from '../IdeasPage';
import CalendarPage from '../CalendarPage';
import BreakdownPage from '../BreakdownPage';
import { IconCalendar, IconFire, IconIdea, IconSkills } from '../icons';

const TABS = [
  ['trends', '热点', IconFire],
  ['ideas', '选题', IconIdea],
  ['calendar', '日历', IconCalendar],
  ['breakdown', '拆解', IconSkills],
] as const;

export function PlanningWorkspace({ page, persona, onUseTopic, onNavigate }: {
  readonly page: Page;
  readonly persona: string;
  readonly onUseTopic: (title: string) => void;
  readonly onNavigate: (page: Page) => void;
}) {
  const active = TABS.some(([id]) => id === page) ? page : 'trends';
  return <div className="section-workspace">
    <nav className="section-nav" aria-label="策划">
      {TABS.map(([id, label, Icon]) => <button key={id}
        className={`section-nav-tab ${active === id ? 'active' : ''}`}
        aria-current={active === id ? 'page' : undefined}
        onClick={() => onNavigate(id)}><Icon size={15} />{label}</button>)}
    </nav>
    <div className="section-body">
      {active === 'trends' && <TrendsPage persona={persona} onUseTopic={onUseTopic} />}
      {active === 'ideas' && <IdeasPage onUseTopic={onUseTopic} />}
      {active === 'calendar' && <CalendarPage persona={persona} />}
      {active === 'breakdown' && <BreakdownPage persona={persona} />}
    </div>
  </div>;
}
