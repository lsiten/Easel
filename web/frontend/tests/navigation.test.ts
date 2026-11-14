import { expect, test } from 'bun:test';
import {
  contentSectionFor,
  moduleForPage,
  normalizePage,
  pageFromHash,
  primaryPageFor,
  routeForPage,
  workspaceSectionFor,
} from '../src/lib/navigation';

test.each([
  ['ai', 'create'],
  ['accounts', 'platform'],
  ['channels', 'platform'],
  ['publish', 'platform'],
  ['video', 'create-video'],
] as const)('legacy page %s resolves to %s', (legacy, current) => {
  expect(normalizePage(legacy)).toBe(current);
});

test.each([
  ['#/dashboard', 'dashboard'],
  ['#/chat', 'chat'],
  ['#/ai', 'create'],
  ['#/accounts', 'platform'],
  ['#/channels', 'platform'],
  ['#/publish', 'platform'],
  ['#/outputs', 'outputs'],
  ['#/assets', 'assets'],
  ['#/video', 'create-video'],
  ['#/content/video', 'video-projects'],
  ['#/settings/profiles', 'profile'],
  ['#/profile', 'profile'],
] as const)('legacy hash %s remains readable', (hash, page) => {
  expect(pageFromHash(hash)).toBe(page);
});

test('nested routes resolve their primary area, module, and section without changing page identity', () => {
  expect(primaryPageFor('ideas')).toBe('dashboard');
  expect(workspaceSectionFor('ideas')).toBe('planning');
  expect(primaryPageFor('generated-assets')).toBe('content');
  expect(contentSectionFor('generated-assets')).toBe('generated');
  expect(moduleForPage('generated-assets')).toBe('content');
  expect(moduleForPage('create-video')).toBe('workspace');
  expect(primaryPageFor('platform')).toBe('dashboard');
  expect(workspaceSectionFor('platform')).toBe('platform');
  expect(workspaceSectionFor('profile')).toBe('profile');
  expect(contentSectionFor('platform')).toBeNull();
  expect(moduleForPage('profile')).toBe('workspace');
  expect(routeForPage('profile')).toBe('#/workspace/profile');
  expect(pageFromHash('#/workspace/profile')).toBe('profile');
  expect(routeForPage('create-video')).toBe('#/workspace/create/video');
  expect(routeForPage('generated-assets')).toBe('#/content/generated/assets');
  expect(routeForPage('video-projects')).toBe('#/content/projects?type=video');
  expect(pageFromHash('#/content/projects?type=video')).toBe('outputs');
  expect(contentSectionFor('video-projects')).toBe('projects');
});
