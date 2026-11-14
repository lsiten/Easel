import { expect, test } from 'bun:test';
import { automaticPublishingAllowed, resolvePublishingMode } from '../src/lib/publishingPolicy';

test.each([
  [{ mode: 'automated' as const, automaticSubmission: true }, true],
  [{ mode: 'automated' as const, automaticSubmission: false }, false],
  [{ mode: 'manual' as const, automaticSubmission: true }, false],
  [{ mode: 'manual' as const, automaticSubmission: false }, false],
] as const)('automatic publishing follows the administrator policy', (policy, expected) => {
  expect(automaticPublishingAllowed(policy)).toBe(expected);
});

test('requested automatic mode falls back to manual whenever automatic submission is unavailable', () => {
  expect(resolvePublishingMode({ mode: 'automated', automaticSubmission: true }, 'automated')).toBe('automated');
  expect(resolvePublishingMode({ mode: 'automated', automaticSubmission: false }, 'automated')).toBe('manual');
  expect(resolvePublishingMode({ mode: 'manual', automaticSubmission: true }, 'automated')).toBe('manual');
});
