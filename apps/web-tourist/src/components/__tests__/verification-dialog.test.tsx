/**
 * §24.3's verification step, as a dialog.
 *
 * The behaviours worth defending are the ones that cost a real person a real
 * attempt. A code has five guesses before it is burned, so a component that
 * submits the same value twice — once when the last digit lands and again when
 * the button is pressed — spends two of them on one mistake. That is invisible
 * on screen and infuriating in use.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { VerificationDialog } from '@/components/auth/verification-dialog';

const verifyEmailCode = vi.fn();
const resendVerification = vi.fn();
const login = vi.fn();

vi.mock('@/lib/auth', () => ({
  verifyEmailCode: (...args: unknown[]) => verifyEmailCode(...args),
  resendVerification: (...args: unknown[]) => resendVerification(...args),
  login: (...args: unknown[]) => login(...args),
}));

function renderDialog(onVerified = vi.fn()) {
  render(
    <VerificationDialog email="ada@example.com" password="a-passphrase" onVerified={onVerified} />,
  );
  return onVerified;
}

function boxes() {
  return Array.from({ length: 6 }, (_, index) =>
    screen.getByLabelText(`Digit ${index + 1}`),
  ) as HTMLInputElement[];
}

function type(code: string) {
  const inputs = boxes();
  for (let index = 0; index < code.length; index += 1) {
    fireEvent.change(inputs[index]!, { target: { value: code[index] } });
  }
}

beforeEach(() => {
  verifyEmailCode.mockReset().mockResolvedValue(undefined);
  resendVerification.mockReset().mockResolvedValue(undefined);
  login.mockReset().mockResolvedValue(undefined);
});

describe('entering the code', () => {
  it('submits once the sixth digit lands, without a button press', async () => {
    renderDialog();
    type('418209');
    await waitFor(() => expect(verifyEmailCode).toHaveBeenCalledWith('ada@example.com', '418209'));
  });

  it('accepts all six digits pasted into the first box', async () => {
    // What people actually do with a code they copied out of an email.
    renderDialog();
    fireEvent.change(boxes()[0]!, { target: { value: '418209' } });
    await waitFor(() => expect(verifyEmailCode).toHaveBeenCalledWith('ada@example.com', '418209'));
  });

  it('ignores anything that is not a digit', () => {
    renderDialog();
    fireEvent.change(boxes()[0]!, { target: { value: 'a' } });
    expect(boxes()[0]!.value).toBe('');
  });

  it('spends exactly one attempt on a completed code', async () => {
    /**
     * The property that matters most here, because the cost of getting it
     * wrong is silent: a code allows five guesses before it is burned, so a
     * component that submits twice spends two of them on one entry and the
     * tourist runs out having typed three codes.
     *
     * The hazard is real rather than theoretical — the completion check runs
     * inside a `setDigits` updater, and React invokes updaters twice in
     * development to surface exactly this kind of side effect.
     */
    renderDialog();
    type('418209');

    await waitFor(() => expect(verifyEmailCode).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(verifyEmailCode).toHaveBeenCalledTimes(1);
  });
});

describe('when the code is wrong', () => {
  it('reports it and clears the boxes for another go', async () => {
    verifyEmailCode.mockRejectedValue(new Error('That code is invalid or has expired.'));
    renderDialog();
    type('000000');

    expect(await screen.findByRole('alert')).toBeDefined();
    await waitFor(() => expect(boxes()[0]!.value).toBe(''));
  });

  it('lets the same code be retried after a failure', async () => {
    // The duplicate guard must not outlive the attempt it was guarding: a
    // network failure on the first try would otherwise wedge the dialog.
    verifyEmailCode.mockRejectedValueOnce(new Error('nope')).mockResolvedValue(undefined);
    renderDialog();
    type('418209');
    await screen.findByRole('alert');

    type('418209');
    await waitFor(() => expect(verifyEmailCode).toHaveBeenCalledTimes(2));
  });
});

describe('after a correct code', () => {
  it('signs the tourist in and hands control back', async () => {
    const onVerified = renderDialog();
    type('418209');
    await waitFor(() => expect(login).toHaveBeenCalledWith({
      email: 'ada@example.com',
      password: 'a-passphrase',
    }));
    await waitFor(() => expect(onVerified).toHaveBeenCalled());
  });

  it('says the account is ready when the sign-in fails', async () => {
    /**
     * Verified but not signed in is a real outcome, and reporting it as a
     * failure would tell somebody their verification did not work when it did
     * — and send them to request a second code that cannot help.
     */
    login.mockRejectedValue(new Error('network'));
    const onVerified = renderDialog();
    type('418209');

    expect(await screen.findByText(/Your email is verified/)).toBeDefined();
    expect(screen.getByRole('link', { name: /Sign in/ })).toBeDefined();
    expect(onVerified).not.toHaveBeenCalled();
  });
});

describe('resending', () => {
  it('asks for a new code and clears what was typed', async () => {
    renderDialog();
    type('418');
    fireEvent.click(screen.getByRole('button', { name: /Send another/ }));

    await waitFor(() => expect(resendVerification).toHaveBeenCalledWith('ada@example.com'));
    expect(boxes()[0]!.value).toBe('');
  });

  it('confirms rather than leaving the button looking unpressed', async () => {
    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: /Send another/ }));
    expect(await screen.findByText(/A new code is on its way/)).toBeDefined();
  });
});

describe('the dialog itself', () => {
  it('is a modal with no way to dismiss it', () => {
    /**
     * Deliberate: registration has already succeeded and the form is gone, so
     * a close control would leave a tourist holding an account they cannot use
     * and no route back to this screen.
     */
    renderDialog();
    const dialog = screen.getByRole('dialog');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(screen.queryByRole('button', { name: /close|cancel/i })).toBeNull();
  });
});
