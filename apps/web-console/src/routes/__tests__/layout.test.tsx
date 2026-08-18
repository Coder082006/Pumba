import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { ConsoleLayout } from '../layout';

const routes: RouteObject[] = [
  {
    path: '/',
    element: <ConsoleLayout />,
    children: [
      { path: 'provider', element: <p>provider tree</p> },
      { path: 'admin', element: <p>admin tree</p> },
    ],
  },
];

function renderAt(path: string) {
  return render(
    <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />,
  );
}

describe('ConsoleLayout', () => {
  it('exposes both role-scoped trees', () => {
    // SRS §34.5: one code base, two role-scoped route trees.
    renderAt('/provider');
    expect(screen.getByRole('link', { name: 'Provider portal' })).toBeDefined();
    expect(screen.getByRole('link', { name: 'Administration' })).toBeDefined();
  });

  it('renders only the tree matching the current route', () => {
    renderAt('/admin');
    expect(screen.getByText('admin tree')).toBeDefined();
    expect(screen.queryByText('provider tree')).toBeNull();
  });
});
