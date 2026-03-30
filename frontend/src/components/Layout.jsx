import { Outlet, useLocation } from 'react-router-dom';
import { useState } from 'react';
import NavBar from './NavBar';
import { Menu } from 'lucide-react';

const PAGE_TITLES = {
  '/': 'Dashboard',
  '/betting': 'SGM Builder',
  '/history': 'Bet History',
  '/csv': 'CSV Paste',
  '/multi': 'Multi Builder',
  '/json': 'JSON Upload',
};

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const location = useLocation();
  const pageTitle = PAGE_TITLES[location.pathname] || 'BotOps';

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-primary)' }}>
      <NavBar sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen((o) => !o)} />

      <div className="main-content">
        {/* Top bar - mobile hamburger + page title */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '16px 24px',
            borderBottom: '1px solid var(--border)',
            background: 'var(--bg-primary)',
            position: 'sticky',
            top: 0,
            zIndex: 20,
          }}
        >
          <button
            className="lg:hidden btn-ghost"
            onClick={() => setSidebarOpen((o) => !o)}
            style={{ padding: 6, borderRadius: 'var(--radius-sm)', marginRight: 4 }}
            aria-label="Open menu"
          >
            <Menu size={20} style={{ color: 'var(--text-secondary)' }} />
          </button>
          <h1 className="page-title">{pageTitle}</h1>
        </div>

        {/* Page content */}
        <main style={{ padding: 24 }}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
