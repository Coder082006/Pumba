'use client';

import { StatusBadge, type StatusTone } from '@pumba/ui';
import { useQuery } from '@tanstack/react-query';

import { fetchHealth } from '@/lib/health';

function toneFor(status: string | undefined): StatusTone {
  if (status === 'ok') return 'success';
  if (status === 'degraded') return 'danger';
  return 'neutral';
}

export function HealthPanel() {
  const { data, isPending, error } = useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
  });

  if (isPending) {
    return <div className="mt-2 rounded-lg border p-4 text-sm text-muted-foreground">Checking…</div>;
  }

  if (error) {
    return (
      <div className="mt-2 rounded-lg border p-4 text-sm text-destructive">{error.message}</div>
    );
  }

  return (
    <div className="mt-2 space-y-3 rounded-lg border p-4">
      <div className="flex items-center gap-3">
        <StatusBadge tone={toneFor(data.status)} label={data.status.toUpperCase()} />
        <span className="text-sm text-muted-foreground">API v{data.version}</span>
      </div>
      <ul className="space-y-1 text-sm">
        {Object.entries(data.checks).map(([name, check]) => (
          <li key={name} className="flex items-center gap-2">
            <StatusBadge
              tone={check.ok ? 'success' : 'danger'}
              label={check.ok ? 'up' : 'down'}
            />
            <span>{name}</span>
            {check.error ? (
              <span className="text-muted-foreground">— {check.error}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
