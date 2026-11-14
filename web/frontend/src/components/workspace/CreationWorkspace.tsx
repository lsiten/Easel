import type { Page } from '../../lib/navigation';
import { AiCreation } from '../ai/AiCreation';
import { AiDrafts } from '../ai/AiDrafts';
import { AiMemory } from '../ai/AiMemory';
import VideoPage from '../VideoPage';
import { IconImage, IconLayers, IconText, IconVideo } from '../icons';

const TABS = [
  ['create', '生成内容', IconImage],
  ['create-drafts', '批量生成', IconLayers],
  ['create-memory', '内容记忆', IconText],
  ['create-video', '视频制作', IconVideo],
] as const;

export function CreationWorkspace({ page, persona, session, onMakeVideo, onNavigate }: {
  readonly page: Page;
  readonly persona: string;
  readonly session?: string;
  readonly onMakeVideo: () => void;
  readonly onNavigate: (page: Page) => void;
}) {
  const active = TABS.some(([id]) => id === page) ? page : 'create';
  return <div className="section-workspace">
    <nav className="section-nav" aria-label="创作">
      {TABS.map(([id, label, Icon]) => <button key={id}
        className={`section-nav-tab ${active === id ? 'active' : ''}`}
        aria-current={active === id ? 'page' : undefined}
        onClick={() => onNavigate(id)}><Icon size={15} />{label}</button>)}
    </nav>
    <div className="section-body section-body-scroll">
      {!persona && active !== 'create' && <div className="panel">请先在侧栏选择画像，再使用创作能力。</div>}
      {persona && active === 'create' && <AiCreation persona={persona} />}
      {persona && active === 'create-drafts' && <AiDrafts persona={persona} />}
      {persona && active === 'create-memory' && <AiMemory persona={persona} />}
      {active === 'create-video' && <VideoPage session={session} persona={persona} onMakeVideo={onMakeVideo} />}
    </div>
  </div>;
}
