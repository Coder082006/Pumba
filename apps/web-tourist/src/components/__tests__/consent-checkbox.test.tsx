/**
 * The consent control, and the registration form that depends on it.
 *
 * Two questions, both worth an assertion rather than an inspection:
 *
 *   1. Does registration still submit now that the dead `/terms` link is gone?
 *      Removing a link from inside a required control is exactly the kind of
 *      edit that can leave the control unreachable or the form permanently
 *      disabled.
 *   2. Does the consent text still say what is being agreed to? A checkbox
 *      with no referent is its own problem — arguably a worse one than a
 *      broken link, because a broken link at least admits a document exists.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ConsentCheckbox } from '@/components/auth/consent-checkbox';

// The page reads `useRouter` so it can send a verified tourist to the landing
// page (§24.3's "→ Home"). There is no App Router in jsdom, and the real hook
// throws rather than returning a no-op.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/register',
}));

describe('a consent document that exists', () => {
  it('links to it from the control itself', () => {
    render(
      <ConsentCheckbox
        id="terms"
        document={{ name: 'terms of use', href: '/explore' }}
        checked={false}
        onCheckedChange={() => {}}
      />,
    );
    const link = screen.getByRole('link', { name: 'terms of use' });
    expect(link.getAttribute('href')).toBe('/explore');
    // No gap notice: there is nothing missing.
    expect(screen.queryByRole('note')).toBeNull();
  });
});

describe('a consent document that is not published', () => {
  it('still names what is being agreed to', () => {
    // The referent question. "I accept." would be meaningless; the name is
    // the minimum, and it is what the eventual link will read.
    render(
      <ConsentCheckbox
        id="terms"
        document={{ name: 'terms of use', pending: true }}
        checked={false}
        onCheckedChange={() => {}}
      />,
    );
    expect(screen.getByText(/I accept the terms of use\./)).toBeDefined();
  });

  it('says the document is not published, rather than implying it is', () => {
    // Silence is what the previous fix left behind: "I accept the terms of
    // use." with nothing behind it reads as though a document exists
    // somewhere. It does not.
    render(
      <ConsentCheckbox
        id="terms"
        document={{ name: 'terms of use', pending: true }}
        checked={false}
        onCheckedChange={() => {}}
      />,
    );
    expect(screen.getByRole('note').textContent).toContain('not published yet');
  });

  it('offers no link, because there is nothing to open', () => {
    render(
      <ConsentCheckbox
        id="terms"
        document={{ name: 'terms of use', pending: true }}
        checked={false}
        onCheckedChange={() => {}}
      />,
    );
    expect(screen.queryByRole('link')).toBeNull();
  });
});

describe('the control', () => {
  it('is operable and reports its state', () => {
    const onCheckedChange = vi.fn();
    render(
      <ConsentCheckbox
        id="terms"
        document={{ name: 'terms of use', pending: true }}
        checked={false}
        onCheckedChange={onCheckedChange}
      />,
    );
    fireEvent.click(screen.getByRole('checkbox'));
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });

  it('is reachable by its label text, not only by the box', () => {
    // §29's Accessibility ≥ 95 gate, and the practical point: the label is
    // the large target, and a screen reader announces the control by it.
    render(
      <ConsentCheckbox
        id="terms"
        document={{ name: 'terms of use', pending: true }}
        checked={false}
        onCheckedChange={() => {}}
      />,
    );
    expect(screen.getByLabelText(/I accept the terms of use/)).toBeDefined();
  });
});

describe('the registration form still works with the link removed', () => {
  /**
   * The question the removal actually raises. `/terms` lived *inside* a
   * required control: if taking it out left the checkbox unreachable or the
   * submit permanently disabled, registration would be broken by the fix, and
   * nothing else in the suite exercises this form.
   */
  async function renderRegister() {
    vi.doMock('@/lib/auth', () => ({
      register: vi.fn().mockResolvedValue({
        user: { public_id: 'x', email: 'a@b.co', status: 'PENDING' },
        verification_required: true,
      }),
      fieldErrorsFrom: () => null,
    }));
    const { default: RegisterPage } = await import('@/app/(auth)/register/page');
    return render(<RegisterPage />);
  }

  it('starts with the submit disabled, as §24.3 requires', () => {
    // "terms must be accepted" — the gate has to still be a gate.
    return renderRegister().then(() => {
      expect(screen.getByRole('button', { name: /Create account/ })).toHaveProperty(
        'disabled',
        true,
      );
    });
  });

  it('enables the submit once consent is given', async () => {
    await renderRegister();
    fireEvent.click(screen.getByLabelText(/I accept the terms of use/));
    expect(screen.getByRole('button', { name: /Create account/ })).toHaveProperty(
      'disabled',
      false,
    );
  });

  it('still tells the user what they are agreeing to', async () => {
    await renderRegister();
    expect(screen.getByText(/I accept the terms of use\./)).toBeDefined();
  });
});
