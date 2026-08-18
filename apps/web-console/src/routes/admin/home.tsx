import { HealthCard } from '../health-card';

export function AdminHome() {
  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Administration</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Phase 1 placeholder. Catalogue, verification, bookings, refunds, commissions, reporting
          and the audit log arrive in Phase 9 (SRS §27).
        </p>
      </div>
      <HealthCard />
    </section>
  );
}
