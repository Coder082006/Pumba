/**
 * `Money` and `LocalTime` — the two components that make SRS §7.2
 * unviolatable rather than documented.
 *
 * Both defend against a value that is *wrong without looking wrong*: a price
 * that has been through an IEEE double, and a departure time rendered in the
 * reader's zone instead of the destination's. Neither produces an error;
 * both produce a number somebody acts on.
 *
 * Moved here from `apps/web-tourist`, where they lived because that package
 * already had jsdom configured. That worked by accident — a test in the
 * consuming app cannot mock a component's *own* dependencies, which is
 * exactly how the map's tile failures went untested for three phases. These
 * two need no mocks, so nothing was broken; they belong beside the code
 * regardless, and the package that ships them now proves them.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LocalTime } from "./local-time";
import { Money } from "./money";

describe("Money", () => {
  it("formats from the decimal string without a float round-trip", () => {
    // 0.1 + 0.2 is the canonical float failure. This amount has more
    // significant digits than a double can hold exactly, so a number
    // conversion anywhere in the path would show a different figure.
    render(
      <Money
        value={{ amount: "10000000000000000.05", currency: "USD" }}
        locale="en-GB"
      />,
    );
    expect(screen.getByText(/10,000,000,000,000,000\.05/)).toBeDefined();
  });

  it("falls back to the raw string rather than a wrong figure", () => {
    render(
      <Money
        value={{ amount: "12.50", currency: "NOTACURRENCY" }}
        locale="en-GB"
      />,
    );
    expect(screen.getByText("NOTACURRENCY 12.50")).toBeDefined();
  });
});

describe("LocalTime", () => {
  // 08:30 in Zanzibar (UTC+3) is 05:30 UTC. A viewer in London must still
  // read 08:30, which is the whole point of §7.2 — and the number that would
  // silently be wrong if the component used the browser's zone.
  const departure = "2027-08-12T05:30:00Z";

  it("renders in the destination zone, not the runtime zone", () => {
    render(
      <LocalTime
        value={departure}
        timeZone="Africa/Dar_es_Salaam"
        display="time"
        locale="en-GB"
      />,
    );
    expect(screen.getByText("08:30")).toBeDefined();
  });

  it("renders the same instant differently for a different destination", () => {
    render(
      <LocalTime
        value={departure}
        timeZone="Europe/London"
        display="time"
        locale="en-GB"
      />,
    );
    expect(screen.getByText("06:30")).toBeDefined();
  });

  it("carries the unambiguous instant in the datetime attribute", () => {
    const { container } = render(
      <LocalTime
        value={departure}
        timeZone="Africa/Dar_es_Salaam"
        display="time"
      />,
    );
    expect(container.querySelector("time")?.getAttribute("dateTime")).toBe(
      "2027-08-12T05:30:00.000Z",
    );
  });

  it("falls back to UTC and says so when the zone is unknown", () => {
    // Never to the viewer's zone: that would be wrong without looking wrong.
    render(
      <LocalTime
        value={departure}
        timeZone="Mars/Olympus"
        display="time"
        locale="en-GB"
      />,
    );
    expect(screen.getByText(/05:30 UTC/)).toBeDefined();
  });

  it('shows the raw value rather than "Invalid Date"', () => {
    render(
      <LocalTime value="not-a-timestamp" timeZone="Africa/Dar_es_Salaam" />,
    );
    expect(screen.getByText("not-a-timestamp")).toBeDefined();
  });
});
