import { HealthCard } from '../health-card';

export function ProviderHome() {
  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Provider portal</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Phase 1 placeholder. Listings, availability calendar, bookings, earnings and payouts
          arrive in Phase 9 (SRS §26).
        </p>
      </div>
      <HealthCard />
    </section>
  );
}
