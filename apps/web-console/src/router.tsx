import { createBrowserRouter, Navigate, type RouteObject } from 'react-router-dom';

import { ConsoleLayout } from './routes/layout';
import { ConsoleLogin } from './routes/login';

/**
 * Two role-scoped route trees in one application — SRS §34.5, and the brief's
 * "single app, two role-scoped route trees".
 *
 * Both trees are lazy so the provider bundle is never shipped to an
 * administrator and vice versa. SRS §34.5 names the single-bundle boundary as
 * this app's one real drawback, so the split is here from the first commit
 * rather than retrofitted once it hurts.
 *
 * The provider and admin trees still render placeholders. Phase 2 adds the
 * login route and the session module behind it; the route *guards* that
 * redirect an unauthenticated visitor arrive with the first real console
 * screen, because a guard with nothing to protect is untestable and a stub
 * that always allows is worse than none.
 */
// Annotated explicitly: pnpm's nested store means the inferred router type
// cannot be named portably (TS2742).
const routes: RouteObject[] = [
  {
    path: '/',
    element: <ConsoleLayout />,
    children: [
      { index: true, element: <Navigate to="/provider" replace /> },
      // Outside the role-scoped trees: signing in is what produces the role.
      { path: 'login', element: <ConsoleLogin /> },
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

// Annotated rather than inferred. React Router's factory returns a type that
// lives in `@remix-run/router`, a transitive dependency, so under
// `declaration`-style checking TypeScript refuses to name it (TS2742) and
// asks for the annotation. Writing it in terms of the factory keeps the type
// exact without importing from a package this app does not depend on directly.
export const router: ReturnType<typeof createBrowserRouter> = createBrowserRouter(routes);
