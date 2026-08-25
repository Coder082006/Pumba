import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/**
 * `packages/ui` shipped four components and no test harness — Map, Gallery,
 * Money and LocalTime were all exercised from `apps/web-tourist` instead.
 * That worked only by accident: a component's own dependencies resolve from
 * *this* package, so a test in the app cannot mock them. The map's tile
 * failures went untested for exactly that reason.
 */
export default defineConfig({
  plugins: [react()],
  test: { environment: 'jsdom', globals: true },
});
