import { expect, test } from 'bun:test';
import type { OutputNode, TrendGroup } from '../src/lib/api';
import {
  filterProjects,
  projectNodes,
  recommendedTrends,
  referenceNodes,
  videoProjects,
} from '../src/lib/contentOrganization';

const file = (path: string, kind: 'text' | 'image' | 'video'): OutputNode => ({
  name: path.split('/').pop() || path,
  path,
  type: 'file',
  kind,
});

const project = (name: string, children: OutputNode[], kind: 'video' | 'article' | 'mixed' = 'mixed'): OutputNode => ({
  name,
  path: name,
  type: 'dir',
  fileCount: children.length,
  children,
  catalog: {
    libraryKind: 'project',
    visibleInProjects: children.length > 0,
    projectKind: kind,
    projectKindLabel: kind,
    status: 'draft',
    deliverableCount: children.length,
    processFileCount: 0,
    mediaCount: children.filter((item) => item.kind === 'video' || item.kind === 'image').length,
    videoCount: children.filter((item) => item.kind === 'video').length,
    imageCount: children.filter((item) => item.kind === 'image').length,
    textCount: children.filter((item) => item.kind === 'text').length,
    needsAttention: false,
    hiddenReason: '',
  },
});

const library = (name: string, libraryKind: 'reference' | 'assets'): OutputNode => ({
  name,
  path: libraryKind === 'reference' ? '@kb' : '@assets',
  type: 'dir',
  catalog: {
    libraryKind,
    visibleInProjects: false,
    projectKind: 'unknown',
    projectKindLabel: libraryKind,
    status: 'reference',
    deliverableCount: 0,
    processFileCount: 0,
    mediaCount: 0,
    videoCount: 0,
    imageCount: 0,
    textCount: 0,
    needsAttention: false,
    hiddenReason: libraryKind,
  },
});

test('project gallery excludes references, asset indexes, and empty residue', () => {
  const roots = [
    project('real', [file('real/final.mp4', 'video')], 'video'),
    project('empty', []),
    library('knowledge', 'reference'),
    library('assets', 'assets'),
  ];

  expect(projectNodes(roots).map((node) => node.name)).toEqual(['real']);
  expect(referenceNodes(roots).map((node) => node.name)).toEqual(['knowledge']);
});

test('video projects are grouped once by project and use the project cover', () => {
  const video = project('video-project', [
    file('video-project/cover.png', 'image'),
    file('video-project/final.mp4', 'video'),
    file('video-project/alt.mp4', 'video'),
  ], 'video');
  video.meta = { title: '成片项目', cover: 'video-project/cover.png' };

  const projects = videoProjects([video, project('article', [file('article/post.md', 'text')], 'article')]);

  expect(projects).toHaveLength(1);
  expect(projects[0]?.node.name).toBe('video-project');
  expect(projects[0]?.cover?.path).toBe('video-project/cover.png');
  expect(projects[0]?.video?.path).toBe('video-project/final.mp4');
  expect(projects[0]?.videoCount).toBe(2);
  expect(projects[0]?.fileCount).toBe(3);
});

test('project type and lifecycle filters reuse the unified gallery', () => {
  const video = project('video-project', [file('video-project/final.mp4', 'video')], 'video');
  video.meta = { status: 'ready' };
  const article = project('article', [file('article/post.md', 'text')], 'article');
  article.meta = { status: 'published' };
  const projects = projectNodes([video, article]);

  expect(filterProjects(projects, 'video').map((node) => node.name)).toEqual(['video-project']);
  expect(filterProjects(projects, 'ready').map((node) => node.name)).toEqual(['video-project']);
  expect(filterProjects(projects, 'published').map((node) => node.name)).toEqual(['article']);
});

test('dashboard recommendations merge platforms and keep the highest persona score first', () => {
  const groups: TrendGroup[] = [
    { platform: 'weibo', label: '微博', items: [{ title: '普通热点', hot: '', url: '', score: 12 }] },
    { platform: 'zhihu', label: '知乎', items: [{ title: '画像热点', hot: '', url: '', score: 42 }] },
  ];

  expect(recommendedTrends(groups, 2)).toEqual([
    expect.objectContaining({ title: '画像热点', platformLabel: '知乎' }),
    expect.objectContaining({ title: '普通热点', platformLabel: '微博' }),
  ]);
});

test('personalized recommendations hide generic hot-list fallback items', () => {
  const groups: TrendGroup[] = [
    {
      platform: 'weibo',
      label: '微博',
      items: [
        { title: '无关热搜', hot: '900万', url: '', score: 80, matched: false },
        { title: '画像相关热点', hot: '10万', url: '', score: 32, matched: true },
      ],
    },
  ];

  expect(recommendedTrends(groups, 6, true).map((item) => item.title)).toEqual(['画像相关热点']);
});
