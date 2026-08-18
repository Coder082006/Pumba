import { StatusBadge, type StatusTone } from '@pumba/ui';
import { useQuery } from '@tanstack/react-query';

import { fetchHealth } from '@/lib/health';

function toneFor(status: string | undefined): StatusTone {
  if (status === 'ok') return 'success';
  if (status === 'degraded') return 'danger';
  return 'neutral';
}

/** Shared by both role trees — proves the generated client works in the SPA. */
export function HealthCard() {
  const { data, isPending, error } = useQuery({ queryKey: ['health'], queryFn: fetchHealth });

  if (isPending) return <p className="text-sm text-muted-foreground">Checking API…</p>;
  if (error) return <p className="text-sm text-destructive">{error.message}</p>;

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div className="flex items-center gap-3">
        <StatusBadge tone={toneFor(data.status)} label={data.status.toUpperCase()} />
        <span className="text-sm text-muted-foreground">API v{data.version}</span>
      </div>
      <ul className="space-y-1 text-sm">
        {Object.entries(data.checks).map(([name, check]) => (
          <li key={name} className="flex items-center gap-2">
            <StatusBadge tone={check.ok ? 'success' : 'danger'} label={check.ok ? 'up' : 'down'} />
            <span>{name}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
