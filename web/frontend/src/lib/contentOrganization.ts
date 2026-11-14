import type { OutputNode, TrendGroup, TrendItem } from './api';

export type VideoProject = {
  readonly node: OutputNode;
  readonly cover: OutputNode | null;
  readonly video: OutputNode | null;
  readonly videoCount: number;
  readonly fileCount: number;
};

export type RecommendedTrend = TrendItem & {
  readonly platform: string;
  readonly platformLabel: string;
};

export const PROJECT_FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'video', label: '视频' },
  { key: 'article', label: '图文' },
  { key: 'image', label: '图片' },
  { key: 'draft', label: '草稿' },
  { key: 'ready', label: '待发' },
  { key: 'published', label: '已发' },
  { key: 'attention', label: '需确认' },
] as const;

export type ProjectFilter = (typeof PROJECT_FILTERS)[number]['key'];

function firstMedia(node: OutputNode, kind?: 'video' | 'image'): OutputNode | null {
  if (node.type === 'file') {
    return (!kind || node.kind === kind) && (node.kind === 'video' || node.kind === 'image') ? node : null;
  }
  for (const child of node.children || []) {
    const found = firstMedia(child, kind);
    if (found) return found;
  }
  return null;
}

function coverFor(node: OutputNode): OutputNode | null {
  const cover = node.meta?.cover;
  if (cover) {
    return {
      name: cover.split('/').pop() || cover,
      path: cover,
      type: 'file',
      kind: /\.(mp4|mov|webm|mkv|m4v)$/i.test(cover) ? 'video' : 'image',
    };
  }
  for (const path of node.meta?.deliverablePaths || []) {
    if (/\.(mp4|mov|webm|mkv|m4v)$/i.test(path)) {
      return { name: path.split('/').pop() || path, path, type: 'file', kind: 'video' };
    }
    if (/\.(png|jpe?g|webp|gif|avif)$/i.test(path)) {
      return { name: path.split('/').pop() || path, path, type: 'file', kind: 'image' };
    }
  }
  return firstMedia(node, 'image') || firstMedia(node, 'video');
}

function collectVideos(node: OutputNode): OutputNode[] {
  if (node.type === 'file') return node.kind === 'video' ? [node] : [];
  return (node.children || []).flatMap(collectVideos);
}

export function projectNodes(roots: readonly OutputNode[]): OutputNode[] {
  return roots.filter((node) => node.type === 'dir' && (
    node.catalog
      ? node.catalog.libraryKind === 'project' && node.catalog.visibleInProjects
      : !node.external && !node.assets && (node.fileCount ?? 1) > 0
  ));
}

export function referenceNodes(roots: readonly OutputNode[]): OutputNode[] {
  return roots.filter((node) => node.catalog?.libraryKind === 'reference');
}

export function filterProjects(
  projects: readonly OutputNode[],
  filter: ProjectFilter,
): OutputNode[] {
  return projects.filter((node) => {
    const catalog = node.catalog;
    switch (filter) {
      case 'all':
        return true;
      case 'video':
        return catalog?.projectKind === 'video' || node.openfilmProject === true
          || node.meta?.kind === 'video';
      case 'article':
        return catalog?.projectKind === 'article';
      case 'image':
        return catalog?.projectKind === 'image';
      case 'draft':
        return (node.meta?.status || catalog?.status) === 'draft';
      case 'ready':
        return (node.meta?.status || catalog?.status) === 'ready';
      case 'published':
        return (node.meta?.status || catalog?.status) === 'published';
      case 'attention':
        return catalog?.needsAttention === true;
      default: {
        const exhaustive: never = filter;
        return exhaustive;
      }
    }
  });
}

export function videoProjects(roots: readonly OutputNode[]): VideoProject[] {
  return projectNodes(roots)
    .flatMap((node) => {
      const videos = collectVideos(node);
      const isVideo = node.catalog?.projectKind === 'video'
        || node.openfilmProject === true
        || node.meta?.kind === 'video'
        || videos.length > 0;
      return isVideo ? [{
        node,
        cover: coverFor(node) || videos[0] || null,
        video: firstMedia(node, 'video') || videos[0] || null,
        videoCount: node.catalog?.videoCount ?? videos.length,
        fileCount: node.fileCount ?? node.catalog?.deliverableCount ?? 0,
      }] : [];
    });
}

export function recommendedTrends(
  groups: readonly TrendGroup[],
  limit: number,
  personalized = false,
): RecommendedTrend[] {
  const candidates = groups
    .flatMap((group) => group.items.map((item) => ({
      ...item,
      platform: group.platform,
      platformLabel: group.label,
    })));
  return (personalized ? candidates.filter((item) => item.matched === true) : candidates)
    .sort((left, right) => (right.score ?? 0) - (left.score ?? 0))
    .slice(0, limit);
}
