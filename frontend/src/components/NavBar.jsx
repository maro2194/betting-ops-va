import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import {
  LayoutDashboard,
  Trophy,
  History,
  FileSpreadsheet,
  Layers,
  Upload,
  Users,
  Sun,
  Moon,
  LogOut,
  Menu,
  X,
  ChevronLeft,
  Zap,
} from 'lucide-react';
import { useState, useEffect } from 'react';

const NAV_GROUPS = [
  {
    label: 'Betting',
    items: [
      { to: '/', label: 'Dashboard', icon: LayoutDashboard },
      { to: '/betting', label: 'SGM Builder', icon: Trophy },
      { to: '/multi', label: 'Multi Builder', icon: Layers },
    ],
  },
  {
    label: 'Services',
    items: [
      { to: '/csb', label: 'CSB', icon: Zap },
    ],
  },
  {
    label: 'Tools',
    items: [
      { to: '/json', label: 'JSON Upload', icon: Upload },
      { to: '/csv', label: 'CSV Paste', icon: FileSpreadsheet },
      { to: '/alloc', label: 'Allocation', icon: Upload },
    ],
  },
  {
    label: 'Multi-Bookie',
    items: [
      { to: '/bookie-accounts', label: 'Bookie Accounts', icon: Users },
    ],
  },
  {
    label: 'Account',
    items: [
      { to: '/history', label: 'History', icon: History },
    ],
  },
];

export default function NavBar({ sidebarOpen, onToggleSidebar }) {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();

  // Close sidebar on route change (mobile)
  useEffect(() => {
    if (sidebarOpen && window.innerWidth < 1024) {
      onToggleSidebar();
    }
  }, [location.pathname]);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const initials = user
    ? (user.name || user.username || '?').split(' ').map((w) => w[0]).join('').toUpperCase().slice(0, 2)
    : '?';

  return (
    <>
      {/* Overlay */}
      <div
        className={`sidebar-overlay ${sidebarOpen ? 'visible' : ''} lg:hidden`}
        onClick={onToggleSidebar}
      />

      {/* Sidebar */}
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        {/* Logo */}
        <div style={{ padding: '20px 16px 8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ flexShrink: 0 }}>
              <rect x="1" y="1" width="30" height="30" rx="7" fill="#0a0a0f" stroke="oklch(55% .22 165)" strokeWidth="1.5" />
              <text x="16" y="21" textAnchor="middle" fill="oklch(55% .22 165)" fontFamily="'JetBrains Mono', monospace" fontWeight="700" fontSize="13">BO</text>
            </svg>
            <span style={{ fontWeight: 700, fontSize: 18, color: 'var(--sidebar-foreground)', letterSpacing: '-0.02em' }}>
              BotOps
            </span>
          </div>
          <button
            className="lg:hidden btn-ghost"
            onClick={onToggleSidebar}
            style={{ padding: 4, borderRadius: 'var(--radius-sm)' }}
            aria-label="Close sidebar"
          >
            <X size={18} style={{ color: 'var(--sidebar-foreground)', opacity: 0.5 }} />
          </button>
        </div>

        {/* Navigation Groups */}
        <nav style={{ flex: 1, padding: '8px 12px', overflowY: 'auto' }}>
          {NAV_GROUPS.map((group) => (
            <div key={group.label}>
              <div className="section-label">{group.label}</div>
              {group.items.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={to === '/'}
                  className={({ isActive }) =>
                    `sidebar-nav-item ${isActive ? 'active' : ''}`
                  }
                >
                  <Icon size={18} />
                  <span>{label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        {/* Bottom section */}
        <div style={{ padding: '12px 16px', borderTop: '1px solid var(--sidebar-border)' }}>
          {/* Theme toggle */}
          <button
            onClick={toggle}
            className="sidebar-nav-item"
            style={{ width: '100%', marginBottom: 8, color: 'var(--sidebar-foreground)' }}
            title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          >
            {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
            <span>{theme === 'dark' ? 'Light Mode' : 'Dark Mode'}</span>
          </button>

          {/* User section */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: '50%',
                background: 'oklch(55% .22 165 / 0.15)',
                color: 'var(--primary)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontWeight: 600,
                fontSize: 12,
                flexShrink: 0,
              }}
            >
              {initials}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 500,
                  color: 'var(--sidebar-foreground)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {user?.name || user?.username || 'User'}
              </div>
            </div>
            <button
              onClick={handleLogout}
              className="btn-ghost"
              style={{ padding: 6, borderRadius: 'var(--radius-sm)', flexShrink: 0 }}
              aria-label="Logout"
              title="Logout"
            >
              <LogOut size={16} style={{ color: 'var(--sidebar-foreground)', opacity: 0.4 }} />
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
