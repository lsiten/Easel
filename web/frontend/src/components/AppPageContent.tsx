import type { ReactNode } from 'react';
import { AgentTasks } from './ai/AgentTasks';
import ChannelsPage from './ChannelsPage';
import DashboardPage from './DashboardPage';
import ProfilePage from './ProfilePage';
import SkillPage from './SkillPage';
import { ConversationWorkspace } from './workspace/ConversationWorkspace';
import { ContentCenter } from './workspace/ContentCenter';
import { CreationWorkspace } from './workspace/CreationWorkspace';
import { PlanningWorkspace } from './workspace/PlanningWorkspace';
import type { CanonicalPage, Page } from '../lib/navigation';
import type { ConfigureApplication } from './channels/PlatformConnectionWorkspace';

type ContentContext = {
  readonly session?: string;
  readonly jumpPath: string;
  readonly onJumpHandled: () => void;
};

type ProfileActions = {
  readonly onUsePersona: (persona: string) => void;
  readonly onNewProfile: () => void;
  readonly onDeleted: (persona: string) => void;
};

type AppPageContentProps = {
  readonly page: CanonicalPage;
  readonly persona: string;
  readonly gatewayStatus: string;
  readonly apiDirect: boolean;
  readonly agentRuntime: string;
  readonly chat: ReactNode;
  readonly content: ContentContext;
  readonly profile: ProfileActions;
  readonly onNavigate: (page: Page) => void;
  readonly onUseTopic: (title: string) => void;
  readonly onMakeVideo: () => void;
  readonly onConfigureApplication: ConfigureApplication;
};

class UnhandledPageError extends Error {
  readonly page: never;

  constructor(page: never) {
    super(`Unhandled application page: ${String(page)}`);
    this.name = 'UnhandledPageError';
    this.page = page;
  }
}

function assertNever(page: never): never {
  throw new UnhandledPageError(page);
}

export function AppPageContent(props: AppPageContentProps) {
  const {
    page, persona, gatewayStatus, apiDirect, agentRuntime, chat, content, profile,
    onNavigate, onUseTopic, onMakeVideo, onConfigureApplication,
  } = props;
  switch (page) {
    case 'dashboard':
      return <DashboardPage persona={persona} gatewayStatus={gatewayStatus} apiDirect={apiDirect}
        agentRuntime={agentRuntime} onNavigate={onNavigate} onUseTopic={onUseTopic} />;
    case 'chat':
    case 'tasks':
      return <ConversationWorkspace page={page} onNavigate={onNavigate} chat={chat}
        tasks={<AgentTasks persona={persona} />} />;
    case 'trends':
    case 'ideas':
    case 'calendar':
    case 'breakdown':
      return <PlanningWorkspace page={page} persona={persona} onUseTopic={onUseTopic} onNavigate={onNavigate} />;
    case 'create':
    case 'create-drafts':
    case 'create-memory':
    case 'create-video':
      return <CreationWorkspace page={page} persona={persona} session={content.session}
        onMakeVideo={onMakeVideo} onNavigate={onNavigate} />;
    case 'platform':
      return <ChannelsPage key={persona} persona={persona} onConfigureApplication={onConfigureApplication} />;
    case 'content':
      return <ContentCenter page="outputs" persona={persona} session={content.session}
        jumpPath={content.jumpPath} onJumpHandled={content.onJumpHandled}
        onNavigate={onNavigate} />;
    case 'outputs':
    case 'assets':
    case 'content-drafts':
    case 'generated-history':
    case 'generated-assets':
    case 'video-projects':
      return <ContentCenter page={page} persona={persona} session={content.session}
        jumpPath={content.jumpPath} onJumpHandled={content.onJumpHandled}
        onNavigate={onNavigate} />;
    case 'skills':
      return <SkillPage persona={persona} />;
    case 'profile':
      return <ProfilePage persona={persona} onUsePersona={profile.onUsePersona}
        onNewProfile={profile.onNewProfile} onDeleted={profile.onDeleted} />;
    default:
      return assertNever(page);
  }
}
