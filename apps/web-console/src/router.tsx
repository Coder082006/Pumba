import { createBrowserRouter, Navigate, type RouteObject } from 'react-router-dom';

import { ConsoleLayout } from './routes/layout';

/**
 * Two role-scoped route trees in one application — SRS §34.5, and the brief's
 * "single app, two role-scoped route trees".
 *
 * Both trees are lazy so the provider bundle is never shipped to an
 * administrator and vice versa. SRS §34.5 names the single-bundle boundary as
 * this app's one real drawback, so the split is here from the first commit
 * rather than retrofitted once it hurts.
 *
 * These routes render placeholders. The role guards that gate them are Phase 2
 * work (RBAC and the ownership predicate pattern, SRS §37.2) — no guard is
 * stubbed here, because a stub that always allows is worse than none.
 */
// Annotated explicitly: pnpm's nested store means the inferred router type
// cannot be named portably (TS2742).
const routes: RouteObject[] = [
  {
    path: '/',
    element: <ConsoleLayout />,
    children: [
      { index: true, element: <Navigate to="/provider" replace /> },
      {
        path: 'provider',
        lazy: async () => {
          const { ProviderHome } = await import('./routes/provider/home');
          return { Component: ProviderHome };
        },
      },
      {
        path: 'admin',
        lazy: async () => {
          const { AdminHome } = await import('./routes/admin/home');
          return { Component: AdminHome };
        },
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
