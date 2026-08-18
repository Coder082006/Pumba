import { apiFetch } from './api';

export interface HealthCheck {
  ok: boolean;
  error: string | null;
}

export interface Health {
  status: string;
  version: string;
  checks: Record<string, HealthCheck>;
}

export function fetchHealth(): Promise<Health> {
  return apiFetch<Health>('/health', { cache: 'no-store' });
}
