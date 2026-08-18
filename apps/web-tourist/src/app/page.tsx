import { ApiRequestError } from '@/lib/api';
import { fetchHealth } from '@/lib/health';
import { HealthPanel } from './health-panel';

/**
 * Phase 1 placeholder.
 *
 * Deliberately a server component: the catalogue and destination pages that
 * replace it in Phase 3 are the SEO surface, and rendering them on the server
 * is the reason the tourist site is Next.js rather than a second Vite SPA.
 * Proving the server-side data path works now is the point of this page.
 */
export default async function HomePage() {
  let health = null;
  let error: string | null = null;

  try {
    health = await fetchHealth();
  } catch (caught) {
    error =
      caught instanceof ApiRequestError
        ? `${caught.code}: ${caught.message}`
        : 'The API is unreachable. Is the Compose stack running?';
  }

  return (
    <main className="container mx-auto max-w-3xl px-4 py-16">
      <h1 className="text-3xl font-semibold tracking-tight">
        Tourism Journey Orchestration Platform
      </h1>
      <p className="mt-3 text-muted-foreground">
        Phase 1 foundation. This page exists to prove the tourist site can reach the API
        through the generated contract, on the server and on the client.
      </p>

      <section className="mt-10 space-y-6">
        <div>
          <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
            Server component
          </h2>
          <div className="mt-2 rounded-lg border p-4">
            {error ? (
              <p className="text-sm text-destructive">{error}</p>
            ) : (
              <pre className="overflow-x-auto text-sm">{JSON.stringify(health, null, 2)}</pre>
            )}
          </div>
        </div>

        <div>
          <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
            Client component (TanStack Query)
          </h2>
          <HealthPanel />
        </div>
      </section>
    </main>
  );
}
