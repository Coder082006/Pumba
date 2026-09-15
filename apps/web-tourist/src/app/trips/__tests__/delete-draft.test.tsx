/**
 * Deleting a draft from My Trips — SRS §24.24.
 *
 * Three things are worth a test and none of them is the request itself: that
 * the button appears against a plan and not against a trip with bookings
 * behind it, that one press does not delete anything, and that a refusal is
 * reported rather than swallowed. The first is the one that matters — the
 * segment a trip is filed under is not the same question as whether the server
 * will delete it, and Drafts holds both kinds.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import MyTripsPage from '@/app/trips/page';
import { ApiRequestError } from '@/lib/api';

const listTrips = vi.fn();
const deleteTrip = vi.fn();
const cancelTripBookings = vi.fn();

vi.mock('@/lib/trips', () => ({
  listTrips: (...args: unknown[]) => listTrips(...args),
  deleteTrip: (...args: unknown[]) => deleteTrip(...args),
}));

vi.mock('@/lib/booking', () => ({
  cancelTripBookings: (...args: unknown[]) => cancelTripBookings(...args),
}));

function trip(overrides: Record<string, unknown> = {}) {
  return {
    public_id: 'trip-1',
    reference: 'TRP-2027-0000001',
    title: 'Zanzibar',
    status: 'DRAFT',
    start_date: '2027-07-01',
    end_date: '2027-07-05',
    adults: 2,
    children: 0,
    currency: 'TZS',
    total_amount: '0.00',
    total_amount_display: null,
    quote_expires_at: null,
    destination: { name: 'Zanzibar', slug: 'zanzibar', timezone: 'Africa/Dar_es_Salaam' },
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('the delete control', () => {
  it('is offered for a plan, and a reservation is offered a cancellation instead', async () => {
    listTrips.mockResolvedValue([
      trip(),
      trip({ public_id: 'trip-2', status: 'PENDING_PAYMENT', title: 'Reserved' }),
    ]);

    render(<MyTripsPage />);

    // Both are filed under Drafts. Deleting a reservation would destroy a
    // booking record, so that row is offered the cancellation instead.
    await waitFor(() => expect(screen.getAllByText(/Delete this plan/)).toHaveLength(1));
    expect(screen.getAllByText(/Cancel this reservation/)).toHaveLength(1);
  });

  it('cancels a reservation rather than deleting it, and files it under Past', async () => {
    listTrips.mockResolvedValue([
      trip({ status: 'PENDING_PAYMENT', title: 'Reserved' }),
    ]);
    cancelTripBookings.mockResolvedValue({ refund_amount: '0.00' });

    render(<MyTripsPage />);
    fireEvent.click(await screen.findByText(/Cancel this reservation/));
    fireEvent.click(screen.getByText(/Yes, cancel it/));

    await waitFor(() => expect(cancelTripBookings).toHaveBeenCalledWith('trip-1'));
    expect(deleteTrip).not.toHaveBeenCalled();
    // The trip is still a trip — it leaves Drafts for Past rather than the
    // list, which is the difference between cancelling and deleting.
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Past (1)' })).toBeTruthy());
    expect(screen.getByRole('tab', { name: 'Drafts (0)' })).toBeTruthy();
    expect(screen.queryByText(/Cancel this reservation/)).toBeNull();
    expect(screen.queryByText(/Delete this plan/)).toBeNull();
  });

  it('takes two presses, and only the second one calls the server', async () => {
    listTrips.mockResolvedValue([trip()]);
    deleteTrip.mockResolvedValue(undefined);

    render(<MyTripsPage />);
    fireEvent.click(await screen.findByText(/Delete this plan/));

    expect(deleteTrip).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText(/Delete for good/));

    await waitFor(() => expect(deleteTrip).toHaveBeenCalledWith('trip-1'));
    // The row goes without the list being read again.
    await waitFor(() => expect(screen.queryByText('Zanzibar')).toBeNull());
    expect(listTrips).toHaveBeenCalledTimes(1);
  });

  it('says what a refusal means rather than reporting a failure', async () => {
    listTrips.mockResolvedValue([trip()]);
    deleteTrip.mockRejectedValue(
      new ApiRequestError(409, {
        code: 'CONFLICT',
        message: 'a trip in PENDING_PAYMENT has bookings behind it',
        details: [],
        request_id: null,
        retryable: false,
      }),
    );

    render(<MyTripsPage />);
    fireEvent.click(await screen.findByText(/Delete this plan/));
    fireEvent.click(screen.getByText(/Delete for good/));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/cancelled rather than deleted/);
    expect(screen.queryByText('Zanzibar')).not.toBeNull();
  });
});
