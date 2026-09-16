import { describe, expect, it } from 'vitest';

import { failureMessage, isSettled } from '@/lib/payments';

/**
 * §21.8's taxonomy in words, and the rule that decides when a screen may stop
 * waiting. Both are pure, and both are the sort of thing that is wrong in a way
 * nobody notices: a tourist told "declined" when their bank asked for a
 * security check will try a different card rather than finishing the one they
 * have.
 */

describe('failureMessage', () => {
  it('tells a tourist what their bank actually said', () => {
    expect(failureMessage('INSUFFICIENT_FUNDS')).toMatch(/insufficient funds/i);
    expect(failureMessage('EXPIRED_INSTRUMENT')).toMatch(/expired/i);
  });

  it('distinguishes an unfinished security check from a refusal', () => {
    // §21.8 gives these different remedies: re-attempt the challenge, or use
    // another card. One sentence for both would send half of them the wrong way.
    expect(failureMessage('AUTHENTICATION_FAILED')).toMatch(/security check/i);
    expect(failureMessage('CARD_DECLINED')).toMatch(/declined/i);
  });

  it('says the places are still held when the gateway is the problem', () => {
    // §21.8: "holds extended by the outage duration". A tourist who thinks
    // they have lost their seats starts again from the planner.
    expect(failureMessage('GATEWAY_UNAVAILABLE')).toMatch(/still held/i);
  });

  it('never leaves a tourist with a code, and never claims a charge', () => {
    const unknown = failureMessage('SOMETHING_NOBODY_MAPPED');

    expect(unknown).not.toMatch(/SOMETHING_NOBODY_MAPPED/);
    expect(unknown).toMatch(/nothing has been charged/i);
  });
});

describe('isSettled', () => {
  const payment = (status: string) => ({ status }) as never;

  it.each(['CAPTURED', 'FAILED', 'EXPIRED'])('%s ends the tourist’s part', (status) => {
    expect(isSettled(payment(status))).toBe(true);
  });

  it.each(['INITIATED', 'PENDING', 'AUTHORISED'])('%s is still in flight', (status) => {
    // A screen that stopped waiting on AUTHORISED would tell a tourist they
    // had paid while the capture was still to come.
    expect(isSettled(payment(status))).toBe(false);
  });
});
