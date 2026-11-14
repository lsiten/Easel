import type { PublishingPolicy } from './api';

export type PublishingMode = 'automated' | 'manual';

export function automaticPublishingAllowed(policy: PublishingPolicy): boolean {
  return policy.mode === 'automated' && policy.automaticSubmission;
}

export function resolvePublishingMode(policy: PublishingPolicy, requested: PublishingMode): PublishingMode {
  return automaticPublishingAllowed(policy) && requested === 'automated' ? 'automated' : 'manual';
}
