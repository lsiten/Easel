export type PrimaryPage = 'dashboard' | 'content' | 'skills';

export type WorkspaceSection = 'overview' | 'conversation' | 'planning' | 'create' | 'platform';
export type ContentSection = 'projects' | 'assets' | 'drafts' | 'generated' | 'video';

export type Page =
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
  | 'generated-history'
  | 'generated-assets'
  | 'content-drafts'
  | 'video-projects'
  | 'profile'
  // Retained as compatibility aliases for old hashes and internal callers.
  | 'ai'
  | 'accounts'
  | 'channels'
  | 'publish'
  | 'video';

const ALIASES = {
  ai: 'create',
  accounts: 'platform',
  channels: 'platform',
  publish: 'platform',
  video: 'create-video',
} as const satisfies Partial<Record<Page, Page>>;

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
  platform: 'platform',
} as const satisfies Partial<Record<Page, WorkspaceSection>>;

const CONTENT_SECTIONS = {
  content: 'projects',
  outputs: 'projects',
  assets: 'assets',
  'content-drafts': 'drafts',
  'generated-history': 'generated',
  'generated-assets': 'generated',
  'video-projects': 'video',
} as const satisfies Partial<Record<Page, ContentSection>>;

const WORKSPACE_HASHES = {
  overview: '#/workspace/overview',
  conversation: '#/workspace/conversation',
  planning: '#/workspace/planning',
  create: '#/workspace/create',
  platform: '#/workspace/platforms',
} as const satisfies Record<WorkspaceSection, `#/${string}`>;

const CONTENT_HASHES = {
  projects: '#/content/projects',
  assets: '#/content/assets',
  drafts: '#/content/drafts',
  generated: '#/content/generated',
  video: '#/content/video',
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
  'video-projects': '#/content/video',
  skills: '#/skills',
  profile: '#/settings/profiles',
} as const satisfies Partial<Record<Page, `#/${string}`>>;

export function normalizePage(page: Page): Page {
  return page in ALIASES ? ALIASES[page as keyof typeof ALIASES] : page;
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

export function routeForPage(page: Page): string {
  const normalized = normalizePage(page);
  if (normalized in PAGE_HASHES) return PAGE_HASHES[normalized as keyof typeof PAGE_HASHES];
  const workspace = workspaceSectionFor(normalized);
  if (workspace) return WORKSPACE_HASHES[workspace];
  const content = contentSectionFor(normalized);
  if (content) return CONTENT_HASHES[content];
  if (normalized === 'skills') return '#/skills';
  if (normalized === 'profile') return '#/settings/profiles';
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
  return PAGE_BY_HASH[hash] ?? null;
}
