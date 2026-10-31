import { describe, expect, it } from 'vitest';

import { readNaturalPolishRetryableFailure } from '../naturalPolish';

describe('readNaturalPolishRetryableFailure', () => {
  it('reads the two stable 去AI味 codes and whether the AI message was given back', () => {
    expect(
      readNaturalPolishRetryableFailure({
        errorCode: 'ERR_NATURAL_POLISH_OUTPUT_UNUSABLE',
        details: { retryable: true, quota_refunded: true },
      }),
    ).toEqual({ kind: 'output', quotaRefunded: true });
    expect(
      readNaturalPolishRetryableFailure({
        errorCode: 'ERR_NATURAL_POLISH_PROVIDER_FAILED',
        details: { retryable: true, quota_refunded: false },
      }),
    ).toEqual({ kind: 'provider', quotaRefunded: false });
  });

  it('never claims a refund the server did not confirm', () => {
    expect(readNaturalPolishRetryableFailure({ errorCode: 'ERR_NATURAL_POLISH_OUTPUT_UNUSABLE' })).toEqual({
      kind: 'output',
      quotaRefunded: false,
    });
  });

  it('leaves quota, generic and non-API errors to their own messages', () => {
    expect(readNaturalPolishRetryableFailure({ errorCode: 'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED' })).toBeNull();
    expect(readNaturalPolishRetryableFailure({ errorCode: 'ERR_INTERNAL_SERVER_ERROR' })).toBeNull();
    expect(readNaturalPolishRetryableFailure(new Error('network'))).toBeNull();
    expect(readNaturalPolishRetryableFailure(null)).toBeNull();
  });
});
