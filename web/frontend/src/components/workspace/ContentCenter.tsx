import type { Page } from '../../lib/navigation';
import { contentSectionFor } from '../../lib/navigation';
import OutputsPage from '../OutputsPage';
import AssetsPage from '../AssetsPage';
import VideoPage from '../VideoPage';
import { ChannelContent } from '../channels/ChannelContent';
import { AiHistory } from '../ai/AiHistory';
import { IconImage, IconLayers } from '../icons';

export function ContentCenter({ page, persona, session, jumpPath, onJumpHandled, onMakeVideo, onNavigate }: {
  readonly page: Page;
  readonly persona: string;
  readonly session?: string;
  readonly jumpPath: string;
  readonly onJumpHandled: () => void;
  readonly onMakeVideo: () => void;
  readonly onNavigate: (page: Page) => void;
}) {
  const section = contentSectionFor(page) ?? 'projects';
  if (!persona && section !== 'projects' && section !== 'generated') {
    return <div className="content-center"><div className="panel">请先在侧栏选择画像，再使用内容资产能力。</div></div>;
  }
  return <div className="content-center">
    <div className="section-body">
      {section === 'projects' && <OutputsPage key={JSON.stringify([session, persona])} session={session}
        jumpPath={jumpPath} onJumpHandled={onJumpHandled} persona={persona} />}
      {section === 'assets' && <AssetsPage key={persona || 'generic'} persona={persona} />}
      {section === 'drafts' && <div className="content-panel-scroll"><ChannelContent persona={persona} /></div>}
      {section === 'generated' && <div className="content-panel-scroll">
        <nav className="section-nav section-nav-inline" aria-label="生成内容">
          <button className={`section-nav-tab ${page === 'generated-history' ? 'active' : ''}`}
            onClick={() => onNavigate('generated-history')}><IconLayers size={15} />生成记录</button>
          <button className={`section-nav-tab ${page === 'generated-assets' ? 'active' : ''}`}
            onClick={() => onNavigate('generated-assets')}><IconImage size={15} />生成素材</button>
        </nav>
        <AiHistory persona={persona} assets={page === 'generated-assets'} />
      </div>}
      {section === 'video' && <VideoPage session={session} persona={persona} onMakeVideo={onMakeVideo} mode="library" />}
    </div>
  </div>;
}
