/**
 * The motion primitives, asserted on rendered output.
 *
 * Two of these are accessibility obligations rather than preferences, and
 * both fail invisibly:
 *
 * - **Reduced motion must stop the cycle, not shorten it.** A component that
 *   respected the setting by animating faster would look correct in review
 *   and be worse for the reader it is meant to protect.
 * - **The pause control must actually pause.** WCAG 2.2.2 asks for a
 *   mechanism, and a button that toggles a label while the timer keeps
 *   running satisfies a checklist and nobody else.
 *
 * The third is a performance obligation: exactly one image may be eager. A
 * hero that eagerly loads five photographs measures five in its Largest
 * Contentful Paint, which is the whole reason I argued against a carousel —
 * so it is pinned here rather than trusted.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CrossfadeHero, Reveal, type HeroImage } from "./motion";

function image(n: number): HeroImage {
  return {
    file_key: `photo-${n}.webp`,
    src: `/media/photo-${n}.webp`,
    srcSet: `/media/photo-${n}-960.webp 960w, /media/photo-${n}.webp 1600w`,
    alt_text: `Zanzibar view ${n}`,
    width: 1600,
    height: 900,
  };
}

/** Drives `prefers-reduced-motion` for a test. */
function setReducedMotion(reduce: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reduce && query.includes("prefers-reduced-motion"),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

/** Captures observers so a test can drive intersection itself. */
function stubIntersectionObserver() {
  const callbacks: IntersectionObserverCallback[] = [];
  class Stub {
    constructor(callback: IntersectionObserverCallback) {
      callbacks.push(callback);
    }
    observe = vi.fn();
    disconnect = vi.fn();
    unobserve = vi.fn();
    takeRecords = vi.fn(() => []);
    root = null;
    rootMargin = "";
    thresholds = [];
  }
  vi.stubGlobal("IntersectionObserver", Stub);
  return callbacks;
}

beforeEach(() => {
  setReducedMotion(false);
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CrossfadeHero", () => {
  it("loads exactly one image eagerly, however many it cycles", () => {
    const { container } = render(
      <CrossfadeHero
        images={[image(1), image(2), image(3)]}
        label="Zanzibar"
      />,
    );
    const eager = [...container.querySelectorAll("img")].filter(
      (img) => img.getAttribute("loading") === "eager",
    );
    expect(eager).toHaveLength(1);
    expect(eager[0]!.getAttribute("src")).toBe("/media/photo-1.webp");
  });

  it("advances on its own", () => {
    const { container } = render(
      <CrossfadeHero
        images={[image(1), image(2)]}
        intervalMs={1000}
        label="Zanzibar"
      />,
    );
    const visible = () =>
      [...container.querySelectorAll("img")].findIndex((img) =>
        img.className.includes("opacity-100"),
      );

    expect(visible()).toBe(0);
    act(() => void vi.advanceTimersByTime(1000));
    expect(visible()).toBe(1);
  });

  it("stops when paused, and resumes when played", () => {
    const { container } = render(
      <CrossfadeHero
        images={[image(1), image(2)]}
        intervalMs={1000}
        label="Zanzibar"
      />,
    );
    const visible = () =>
      [...container.querySelectorAll("img")].findIndex((img) =>
        img.className.includes("opacity-100"),
      );

    act(() => screen.getByRole("button", { name: "Pause" }).click());
    act(() => void vi.advanceTimersByTime(5000));
    expect(visible(), "a paused hero must not advance").toBe(0);

    act(() => screen.getByRole("button", { name: "Play" }).click());
    act(() => void vi.advanceTimersByTime(1000));
    expect(visible()).toBe(1);
  });

  it("never cycles under reduced motion, and offers no controls", () => {
    // The point of the setting. Shortening the interval would be worse than
    // ignoring it: more movement per minute, not less.
    setReducedMotion(true);
    const { container } = render(
      <CrossfadeHero
        images={[image(1), image(2), image(3)]}
        intervalMs={1000}
        label="Zanzibar"
      />,
    );

    act(() => void vi.advanceTimersByTime(30_000));

    const visible = [...container.querySelectorAll("img")].findIndex((img) =>
      img.className.includes("opacity-100"),
    );
    expect(visible).toBe(0);
    expect(screen.queryByRole("button", { name: /pause|play/i })).toBeNull();
  });

  it("names only the first image, leaving the rest decorative", () => {
    const { container } = render(
      <CrossfadeHero images={[image(1), image(2)]} label="Zanzibar" />,
    );
    const imgs = [...container.querySelectorAll("img")];
    expect(imgs[0]!.getAttribute("alt")).toBe("Zanzibar view 1");
    expect(imgs[1]!.getAttribute("alt")).toBe("");
    expect(imgs[1]!.getAttribute("aria-hidden")).toBe("true");
  });

  it("reserves its box even with no photography at all", () => {
    // A market whose pictures have not been added yet. The page must not
    // change shape the day they are.
    const { container } = render(<CrossfadeHero images={[]} label="Arusha" />);
    const section = container.querySelector("section");
    expect(section?.className).toContain("h-hero");
    expect(container.querySelectorAll("img")).toHaveLength(0);
  });

  it("shows no controls for a single image", () => {
    render(<CrossfadeHero images={[image(1)]} label="Zanzibar" />);
    expect(screen.queryByRole("button", { name: /pause/i })).toBeNull();
  });
});

describe("Reveal", () => {
  it("starts hidden and appears when it intersects", () => {
    const callbacks = stubIntersectionObserver();
    const { container } = render(
      <Reveal>
        <p>Stone Town</p>
      </Reveal>,
    );
    const wrapper = container.firstElementChild!;
    expect(wrapper.className).toContain("opacity-0");

    act(() =>
      callbacks[0]!([{ isIntersecting: true }] as never, null as never),
    );
    expect(wrapper.className).toContain("opacity-100");
  });

  it("animates transform and opacity only", () => {
    // Anything else moves the content around it, which is a CLS failure and
    // the reason the CI gate exists.
    const callbacks = stubIntersectionObserver();
    const { container } = render(
      <Reveal from="left">
        <p>Nungwi</p>
      </Reveal>,
    );
    const wrapper = container.firstElementChild!;
    expect(wrapper.className).toContain("transition-[opacity,transform]");
    expect(wrapper.className).toContain("-translate-x-6");

    act(() =>
      callbacks[0]!([{ isIntersecting: true }] as never, null as never),
    );
    expect(wrapper.className).toContain("translate-x-0");
  });

  it("is never hidden under reduced motion", () => {
    // No observer, no animation, content present from the first frame.
    setReducedMotion(true);
    const callbacks = stubIntersectionObserver();
    const { container } = render(
      <Reveal>
        <p>Jozani</p>
      </Reveal>,
    );
    expect(container.firstElementChild!.className).toContain("opacity-100");
    expect(callbacks).toHaveLength(0);
  });

  it("shows its content when IntersectionObserver is unavailable", () => {
    // The degradation that matters: an old browser must get the content, not
    // a permanently invisible block.
    vi.stubGlobal("IntersectionObserver", undefined);
    const { container } = render(
      <Reveal>
        <p>Kendwa</p>
      </Reveal>,
    );
    expect(container.firstElementChild!.className).toContain("opacity-100");
  });
});
