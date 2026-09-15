import { describe, expect, it } from 'vitest';

import { segmentOf, tripStatusLabel } from '@/lib/trip-segments';

const TODAY = '2027-06-15';

function trip(status: string, start: string, end: string) {
  return { status, start_date: start, end_date: end };
}

describe('segmentOf — §24.24', () => {
  it.each(['DRAFT', 'PRICED', 'PENDING_PAYMENT'])('a %s trip is a draft', (status) => {
    expect(segmentOf(trip(status, '2027-07-01', '2027-07-05'), TODAY)).toBe('DRAFTS');
  });

  it('a confirmed trip ahead is upcoming', () => {
    expect(segmentOf(trip('CONFIRMED', '2027-07-01', '2027-07-05'), TODAY)).toBe('UPCOMING');
  });

  it('a confirmed trip inside its dates is active, from its first day', () => {
    expect(segmentOf(trip('CONFIRMED', TODAY, '2027-06-20'), TODAY)).toBe('ACTIVE');
    expect(segmentOf(trip('CONFIRMED', '2027-06-10', TODAY), TODAY)).toBe('ACTIVE');
  });

  it('a trip under way is active whatever its dates say', () => {
    expect(segmentOf(trip('IN_PROGRESS', '2027-07-01', '2027-07-05'), TODAY)).toBe('ACTIVE');
  });

  it('a confirmed trip whose last day has passed is past', () => {
    expect(segmentOf(trip('CONFIRMED', '2027-06-01', '2027-06-14'), TODAY)).toBe('PAST');
  });

  it.each(['COMPLETED', 'CANCELLED'])('a %s trip is past even if it was ahead', (status) => {
    expect(segmentOf(trip(status, '2027-07-01', '2027-07-05'), TODAY)).toBe('PAST');
  });
});

describe('tripStatusLabel', () => {
  it('uses words a tourist reads, not the enum', () => {
    expect(tripStatusLabel('PENDING_PAYMENT')).toBe('Reserved, awaiting payment');
    expect(tripStatusLabel('DRAFT')).toBe('Planning');
  });
});
