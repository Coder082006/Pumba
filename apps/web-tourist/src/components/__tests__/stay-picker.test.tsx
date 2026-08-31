/**
 * §24.11, and the three things this screen deliberately does not do.
 *
 * The tests worth having here are not "does the list render". They are the
 * ones that fail if somebody later relaxes a decision that was made because
 * the data does not exist:
 *
 *   - free entry must not acquire a coordinate or a pin (§13.2);
 *   - free entry must not become saveable while there is no geocoder;
 *   - the BR-101 bound must come from `GET /config`, never from a literal.
 *
 * Each of those is a one-line change away from being wrong and none of them
 * would look wrong on screen, which is exactly when a test earns its place.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { StayPicker } from '@/components/catalogue/stay-picker';
import type * as TripsModule from '@/lib/trips';
import type { Accommodation, Destination } from '@pumba/contracts';

// `AddToTrip` asks for the tourist's trips as soon as it mounts. There is no
// server here, so the call is answered rather than left to time out: this suite
// is about what the picker hands over, not about the network.
vi.mock('@/lib/trips', async (importOriginal) => ({
  ...(await importOriginal<typeof TripsModule>()),
  listTrips: async () => [],
}));

const DESTINATION: Destination = {
  public_id: '11111111-1111-4111-8111-111111111111',
  name: 'Nungwi',
  slug: 'nungwi',
  summary: null,
  description: 'North coast.',
  latitude: '-5.7264',
  longitude: '39.2953',
  timezone: 'Africa/Dar_es_Salaam',
  default_currency: 'TZS',
  is_gateway: false,
  gateway_type: null,
  gateway_code: null,
  launch_date: '2027-01-01',
  feature_rank: 0,
  region: {
    public_id: '22222222-2222-4222-8222-222222222222',
    name: 'Unguja North',
    slug: 'unguja-north',
    country: {
      public_id: '33333333-3333-4333-8333-333333333333',
      iso_code: 'TZ',
      name: 'Tanzania',
      default_currency: 'TZS',
      default_timezone: 'Africa/Dar_es_Salaam',
    },
    market: {
      public_id: '44444444-4444-4444-8444-444444444444',
      name: 'Zanzibar',
      slug: 'zanzibar',
    },
  },
  media: [],
};

function property(name: string, id: string, propertyType = 'HOTEL'): Accommodation {
  return {
    public_id: id,
    name,
    slug: name.toLowerCase().replace(/\s+/g, '-'),
    summary: null,
    description: `${name} description.`,
    property_type: propertyType,
    latitude: '-5.7264',
    longitude: '39.2953',
    address_line: `${name}, Nungwi Beach`,
    check_in_time: '14:00:00',
    check_out_time: '10:00:00',
    feature_rank: 0,
    destination: DESTINATION,
    media: [],
  };
}

const PROPERTIES = [
  property('Kilindi Zanzibar', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'RESORT'),
  property('Kendwa Rocks', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),
];

function renderPicker(overrides: Partial<Parameters<typeof StayPicker>[0]> = {}) {
  return render(
    <StayPicker
      properties={PROPERTIES}
      destinationName="Nungwi"
      maxNights={30}
      map={<div data-testid="map">map</div>}
      {...overrides}
    />,
  );
}

function pickDates(checkIn: string, checkOut: string) {
  fireEvent.change(screen.getByLabelText('Check in'), { target: { value: checkIn } });
  fireEvent.change(screen.getByLabelText('Check out'), { target: { value: checkOut } });
}

describe('the curated path', () => {
  it('lists the destination’s properties with their type', () => {
    renderPicker();
    expect(screen.getByRole('button', { name: /Kilindi Zanzibar/ })).toBeDefined();
    expect(screen.getByRole('button', { name: /RESORT/ })).toBeDefined();
  });

  it('filters the list in place rather than by a round trip', () => {
    renderPicker();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'kendwa' } });
    expect(screen.queryByRole('button', { name: /Kilindi/ })).toBeNull();
    expect(screen.getByRole('button', { name: /Kendwa Rocks/ })).toBeDefined();
  });

  it('summarises the selection with the nights', () => {
    renderPicker();
    pickDates('2027-08-12', '2027-08-16');
    fireEvent.click(screen.getByRole('button', { name: /Kilindi Zanzibar/ }));
    expect(screen.getByText('Kilindi Zanzibar for 4 nights.')).toBeDefined();
  });

  it('offers free entry when nothing matches, rather than a dead end', () => {
    // §24.11 names this state explicitly. An empty list with nothing after it
    // is where a tourist whose hotel is not seeded stops using the product.
    renderPicker();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'zzzz' } });
    expect(screen.getByText(/Nothing matches/)).toBeDefined();
    expect(screen.getByPlaceholderText(/Enter any hotel name or address/)).toBeDefined();
  });

  it('says the list is empty rather than that the search failed', () => {
    renderPicker({ properties: [] });
    expect(screen.getByText(/do not have any properties listed in Nungwi/)).toBeDefined();
  });
});

describe('free entry does not acquire a coordinate', () => {
  it('records the stay by name and says it is not on the map', () => {
    // The decision this file exists to hold. §13.2 forbids persisting an
    // unconfirmed geocode, there is no geocoder to produce a confirmable one,
    // and a "confirm this pin" step over an invented coordinate would be
    // worse than no pin — it would carry the tourist's apparent assent.
    renderPicker();
    pickDates('2027-08-12', '2027-08-16');
    fireEvent.change(screen.getByPlaceholderText(/Enter any hotel name or address/), {
      target: { value: 'Auntie’s guesthouse, Paje' },
    });
    expect(
      screen.getByText('Auntie’s guesthouse, Paje for 4 nights — recorded by name, not placed on the map.'),
    ).toBeDefined();
  });

  it('warns that transfers cannot be planned around it', () => {
    // VR-16's substance, delivered while the tourist can still act on it.
    renderPicker();
    expect(screen.getByText(/won’t be able to plan your airport transfers/)).toBeDefined();
  });

  it('offers no confirmation control for a pin that does not exist', () => {
    renderPicker();
    expect(screen.queryByRole('button', { name: /confirm/i })).toBeNull();
  });
});

describe('BR-101', () => {
  it('refuses a check-out before check-in', () => {
    renderPicker();
    pickDates('2027-08-16', '2027-08-12');
    expect(screen.getByRole('alert').textContent).toContain('Check-out must be after check-in');
  });

  it('refuses a stay longer than the configured bound, naming it', () => {
    renderPicker();
    pickDates('2027-08-01', '2027-09-05');
    expect(screen.getByRole('alert').textContent).toContain('30 nights');
  });

  it('takes the bound from GET /config, not from a literal', () => {
    // NFR-M07. If 30 were hardcoded anywhere in this component, this fails.
    renderPicker({ maxNights: 7 });
    pickDates('2027-08-01', '2027-08-12');
    expect(screen.getByRole('alert').textContent).toContain('7 nights');
  });

  it('says nothing at all while the second date is still empty', () => {
    // The "not finished typing" state is the normal one, not an error.
    renderPicker();
    fireEvent.change(screen.getByLabelText('Check in'), { target: { value: '2027-08-12' } });
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('when GET /config is unreachable', () => {
  it('disables the dates and says why, rather than assuming a bound', () => {
    renderPicker({ maxNights: null });
    expect(screen.getByLabelText('Check in')).toHaveProperty('disabled', true);
    expect(screen.getByText(/Dates are temporarily unavailable/)).toBeDefined();
  });

  it('leaves the properties and the map alone', () => {
    // A settings outage costs the dates, not the page.
    renderPicker({ maxNights: null });
    expect(screen.getByTestId('map')).toBeDefined();
    expect(screen.getByRole('button', { name: /Kilindi Zanzibar/ })).toBeDefined();
  });
});

describe('what can be saved', () => {
  it('offers the curated path once a property and valid dates are chosen', async () => {
    // The `trip` module is no longer a skeleton, so `POST /trips/{id}/items`
    // exists and a curated stay has a coordinate to route from. `AddToTrip`
    // loads the tourist's trips on mount and finds none here, so what it
    // renders is the invitation to plan one — which is the proof it mounted
    // rather than the disabled placeholder.
    renderPicker();
    pickDates('2027-08-12', '2027-08-16');
    fireEvent.click(screen.getByRole('button', { name: /Kilindi Zanzibar/ }));
    expect(await screen.findByRole('link', { name: /Plan a trip first/ })).toBeDefined();
  });

  it('keeps the free-entry path disabled, and says the reason is the map', () => {
    // Unchanged, and now the *only* reason. §13.2 forbids persisting a
    // coordinate nobody confirmed and there is no geocoder, so a stay entered
    // as text has nowhere for §10.4 to route to.
    renderPicker();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'somewhere else' } });
    fireEvent.change(screen.getByLabelText('Hotel name or address'), {
      target: { value: 'A friend’s house' },
    });
    expect(screen.getByRole('button', { name: /Add to trip/ })).toHaveProperty('disabled', true);
    expect(screen.getByText(/cannot have transfers planned around it/)).toBeDefined();
  });

  it('does not offer to save before a property is chosen', () => {
    renderPicker();
    pickDates('2027-08-12', '2027-08-16');
    expect(screen.getByRole('button', { name: /Add to trip/ })).toHaveProperty('disabled', true);
  });
});

describe('“I haven’t booked anywhere yet”', () => {
  it('is a supported answer rather than a failure', () => {
    // §10.6: VR-16 warns, it does not block. The wording matters — a tourist
    // who reads this as an error goes and invents an address.
    renderPicker();
    fireEvent.click(screen.getByRole('checkbox'));
    expect(screen.getByText(/Nights without a stay will be flagged/)).toBeDefined();
  });

  it('clears any property selection rather than holding two answers', () => {
    renderPicker();
    fireEvent.click(screen.getByRole('button', { name: /Kilindi Zanzibar/ }));
    fireEvent.click(screen.getByRole('checkbox'));

    expect(screen.getByText(/No accommodation recorded/)).toBeDefined();
    expect(screen.queryByText(/Kilindi Zanzibar for/)).toBeNull();
    // The list's own state has to follow, or the screen shows a property
    // still highlighted underneath a summary saying nothing is recorded.
    expect(screen.getByRole('button', { name: /Kilindi Zanzibar/ })).toHaveProperty(
      'ariaPressed',
      'false',
    );
  });
});
