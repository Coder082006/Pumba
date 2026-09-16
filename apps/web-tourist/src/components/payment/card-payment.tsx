'use client';

import { Elements, PaymentElement, useElements, useStripe } from '@stripe/react-stripe-js';
import { loadStripe, type Stripe } from '@stripe/stripe-js';
import { useCallback, useState } from 'react';

import { failureMessage, verifyPayment, type Payment } from '@/lib/payments';

/**
 * §24.22's payment step — SRS §21.1, §21.3.
 *
 * **The card field is Stripe's, inside their iframe.** PM1 keeps the platform
 * in PCI SAQ A scope, and the whole of how is visible here: this component
 * receives a client secret and renders `PaymentElement`. No input in this
 * repository ever holds a card number, and none ever should.
 *
 * **3-D Secure is Stripe's redirect, not ours.** `redirect: 'if_required'`
 * means a card that needs a challenge gets one — in their modal, or by leaving
 * the page and coming back — and a card that does not is charged without a
 * round trip the tourist would read as a stall.
 *
 * **Success here is not confirmation.** Stripe answering "succeeded" means the
 * money moved; it does not mean the platform knows. §20.8's routine runs when
 * the webhook lands, so this asks the server to verify (PM4: the PSP is the
 * authority, and the server is the one allowed to ask it) and only then hands
 * over to the caller.
 */

let cached: Promise<Stripe | null> | null = null;

/** The publishable key is public by design — it identifies the account and
 *  authorises nothing. The secret key never leaves the API. */
export function stripeClient(): Promise<Stripe | null> | null {
  const key = process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY;
  if (!key) return null;
  cached ??= loadStripe(key);
  return cached;
}

interface Props {
  payment: Payment;
  clientSecret: string;
  onPaid: (payment: Payment) => void;
}

export function CardPayment({ payment, clientSecret, onPaid }: Props) {
  const stripe = stripeClient();
  if (!stripe) {
    // Configuration, not a failure the tourist caused — and said plainly
    // rather than as a broken form. `.env.example` names the variable.
    return (
      <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">
        Card payment is not configured on this server. Your places are reserved; nothing has
        been charged.
      </p>
    );
  }

  return (
    <Elements stripe={stripe} options={{ clientSecret, appearance: { theme: 'stripe' } }}>
      <PayForm payment={payment} onPaid={onPaid} />
    </Elements>
  );
}

function PayForm({ payment, onPaid }: { payment: Payment; onPaid: (payment: Payment) => void }) {
  const stripe = useStripe();
  const elements = useElements();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const pay = useCallback(async () => {
    if (!stripe || !elements) return;
    setBusy(true);
    setProblem(null);
    try {
      const { error } = await stripe.confirmPayment({
        elements,
        redirect: 'if_required',
        confirmParams: { return_url: window.location.href },
      });
      if (error) {
        setProblem(error.message ?? failureMessage(error.decline_code ?? error.code));
        return;
      }
      // The gateway says it is done. The platform does not know yet, so ask
      // the server rather than claiming a confirmation on Stripe's word.
      onPaid(await verifyPayment(payment.id));
    } catch {
      setProblem(failureMessage('GATEWAY_UNAVAILABLE'));
    } finally {
      setBusy(false);
    }
  }, [stripe, elements, payment.id, onPaid]);

  return (
    <div className="space-y-4">
      <PaymentElement />
      {problem ? (
        <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">
          {problem}
        </p>
      ) : null}
      <button
        type="button"
        disabled={busy || !stripe}
        onClick={() => void pay()}
        className="w-full rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {busy ? 'Paying…' : `Pay ${payment.amount} ${payment.currency}`}
      </button>
      <p className="text-xs text-muted-foreground">
        Your card details go straight to our payment provider and are never stored by us. The
        amount is the total your trip was priced at.
      </p>
    </div>
  );
}
