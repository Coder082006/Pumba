import { NavLink, Outlet } from 'react-router-dom';

import { cn } from '@pumba/ui';

const TABS = [
  { to: '/provider', label: 'Provider portal' },
  { to: '/admin', label: 'Administration' },
];

export function ConsoleLayout() {
  return (
    <div className="min-h-screen font-sans">
      <header className="border-b">
        <nav className="container mx-auto flex gap-1 px-4 py-3">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                cn(
                  'rounded-md px-3 py-2 text-sm font-medium transition-colors',
                  isActive ? 'bg-secondary text-secondary-foreground' : 'hover:bg-secondary/60',
                )
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="container mx-auto px-4 py-10">
        <Outlet />
      </main>
    </div>
  );
}
