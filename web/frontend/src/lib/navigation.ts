export type PrimaryPage = 'dashboard' | 'content' | 'skills';

export type WorkspaceSection = 'overview' | 'conversation' | 'planning' | 'create' | 'profile' | 'platform';
export type ContentSection = 'projects' | 'assets' | 'drafts' | 'generated';

export type LegacyPage = 'ai' | 'accounts' | 'channels' | 'publish' | 'video';

export type CanonicalPage =
  | PrimaryPage
  | 'chat'
  | 'tasks'
  | 'trends'
  | 'ideas'
  | 'calendar'
  | 'breakdown'
  | 'create'
  | 'create-drafts'
  | 'create-memory'
  | 'create-video'
  | 'platform'
  | 'outputs'
  | 'assets'
  | 'generated-history'
  | 'generated-assets'
  | 'content-drafts'
  | 'video-projects'
  | 'profile';

export type Page = CanonicalPage | LegacyPage;

const ALIASES = {
  ai: 'create',
  accounts: 'platform',
  channels: 'platform',
  publish: 'platform',
  video: 'create-video',
} as const satisfies Record<LegacyPage, CanonicalPage>;

const WORKSPACE_SECTIONS = {
  dashboard: 'overview',
  chat: 'conversation',
  tasks: 'conversation',
  trends: 'planning',
  ideas: 'planning',
  calendar: 'planning',
  breakdown: 'planning',
  create: 'create',
  'create-drafts': 'create',
  'create-memory': 'create',
  'create-video': 'create',
  profile: 'profile',
  platform: 'platform',
} as const satisfies Partial<Record<Page, WorkspaceSection>>;

const CONTENT_SECTIONS = {
  content: 'projects',
  outputs: 'projects',
  assets: 'assets',
  'content-drafts': 'drafts',
  'generated-history': 'generated',
  'generated-assets': 'generated',
  'video-projects': 'projects',
} as const satisfies Partial<Record<Page, ContentSection>>;

const WORKSPACE_HASHES = {
  overview: '#/workspace/overview',
  conversation: '#/workspace/conversation',
  planning: '#/workspace/planning',
  create: '#/workspace/create',
  profile: '#/workspace/profile',
  platform: '#/workspace/platforms',
} as const satisfies Record<WorkspaceSection, `#/${string}`>;

const CONTENT_HASHES = {
  projects: '#/content/projects',
  assets: '#/content/assets',
  drafts: '#/content/drafts',
  generated: '#/content/generated',
} as const satisfies Record<ContentSection, `#/${string}`>;

const PAGE_HASHES = {
  dashboard: '#/workspace/overview',
  chat: '#/workspace/conversation',
  tasks: '#/workspace/tasks',
  trends: '#/workspace/planning/trends',
  ideas: '#/workspace/planning/ideas',
  calendar: '#/workspace/planning/calendar',
  breakdown: '#/workspace/planning/breakdown',
  create: '#/workspace/create',
  'create-drafts': '#/workspace/create/batch',
  'create-memory': '#/workspace/create/memory',
  'create-video': '#/workspace/create/video',
  platform: '#/workspace/platforms',
  content: '#/content/projects',
  outputs: '#/content/projects',
  assets: '#/content/assets',
  'content-drafts': '#/content/drafts',
  'generated-history': '#/content/generated',
  'generated-assets': '#/content/generated/assets',
  'video-projects': '#/content/projects?type=video',
  skills: '#/skills',
  profile: '#/workspace/profile',
} as const satisfies Partial<Record<Page, `#/${string}`>>;

function isLegacyPage(page: Page): page is LegacyPage {
  return page in ALIASES;
}

export function normalizePage(page: Page): CanonicalPage {
  return isLegacyPage(page) ? ALIASES[page] : page;
}

export function primaryPageFor(page: Page): PrimaryPage {
  if (page === 'content' || contentSectionFor(page)) return 'content';
  if (page === 'skills') return 'skills';
  return 'dashboard';
}

export function workspaceSectionFor(page: Page): WorkspaceSection | null {
  const normalized = normalizePage(page);
  return normalized in WORKSPACE_SECTIONS
    ? WORKSPACE_SECTIONS[normalized as keyof typeof WORKSPACE_SECTIONS]
    : null;
}

export function contentSectionFor(page: Page): ContentSection | null {
  return page in CONTENT_SECTIONS ? CONTENT_SECTIONS[page as keyof typeof CONTENT_SECTIONS] : null;
}

export function moduleForPage(page: Page): 'workspace' | 'content' | null {
  if (workspaceSectionFor(page)) return 'workspace';
  if (contentSectionFor(page)) return 'content';
  return null;
}

export function routeForPage(page: Page): string {
  const normalized = normalizePage(page);
  if (normalized in PAGE_HASHES) return PAGE_HASHES[normalized as keyof typeof PAGE_HASHES];
  const workspace = workspaceSectionFor(normalized);
  if (workspace) return WORKSPACE_HASHES[workspace];
  const content = contentSectionFor(normalized);
  if (content) return CONTENT_HASHES[content];
  if (normalized === 'skills') return '#/skills';
  return '#/workspace/overview';
}

const PAGE_BY_HASH: Readonly<Record<string, Page>> = {
  '#/workspace/overview': 'dashboard',
  '#/workspace/conversation': 'chat',
  '#/workspace/tasks': 'tasks',
  '#/workspace/planning': 'trends',
  '#/workspace/planning/trends': 'trends',
  '#/workspace/planning/ideas': 'ideas',
  '#/workspace/planning/calendar': 'calendar',
  '#/workspace/planning/breakdown': 'breakdown',
  '#/workspace/create': 'create',
  '#/workspace/create/batch': 'create-drafts',
  '#/workspace/create/memory': 'create-memory',
  '#/workspace/create/video': 'create-video',
  '#/workspace/profile': 'profile',
  '#/workspace/platforms': 'platform',
  '#/content': 'outputs',
  '#/content/projects': 'outputs',
  '#/content/assets': 'assets',
  '#/content/drafts': 'content-drafts',
  '#/content/generated': 'generated-history',
  '#/content/generated/assets': 'generated-assets',
  '#/content/video': 'video-projects',
  '#/skills': 'skills',
  '#/settings/profiles': 'profile',
  '#/dashboard': 'dashboard',
  '#/chat': 'chat',
  '#/ai': 'create',
  '#/accounts': 'platform',
  '#/channels': 'platform',
  '#/publish': 'platform',
  '#/outputs': 'outputs',
  '#/assets': 'assets',
  '#/video': 'create-video',
  '#/profile': 'profile',
};

export function pageFromHash(hash: string): Page | null {
  return PAGE_BY_HASH[hash.split('?')[0]] ?? null;
}
