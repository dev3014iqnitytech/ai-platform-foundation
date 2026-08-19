import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useMsal } from '@azure/msal-react';
import {
  LayoutDashboard, Search, TestTube, CheckSquare, BookOpen,
  ScrollText, Settings, LogOut, ChevronRight, Shield, Bell
} from 'lucide-react';

const navItems = [
  { to: '/',          icon: LayoutDashboard, label: 'Dashboard'      },
  { to: '/stories',   icon: Search,          label: 'Story Search'   },
  { to: '/test-cases',icon: TestTube,        label: 'Test Cases'     },
  { to: '/approvals', icon: CheckSquare,     label: 'Approval Queue' },
  { to: '/knowledge', icon: BookOpen,        label: 'Knowledge Base' },
  { to: '/audit',     icon: ScrollText,      label: 'Audit Logs'     },
  { to: '/admin',     icon: Settings,        label: 'Admin'          },
];

export function AppShell() {
  const { instance, accounts } = useMsal();
  const navigate = useNavigate();
  const user = accounts[0];

  const handleLogout = () => {
    instance.logoutRedirect({ postLogoutRedirectUri: '/' });
  };

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem' }}>
            <div style={{
              width: 36, height: 36, borderRadius: 10,
              background: 'linear-gradient(135deg, #3b82f6, #6366f1)',
              display: 'flex', alignItems: 'center', justifyContent: 'center'
            }}>
              <TestTube size={18} color="white" />
            </div>
            <div>
              <h2 style={{ fontSize: '0.9rem', lineHeight: 1 }}>EATAP</h2>
              <p style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', marginTop: 2 }}>
                Enterprise AI Platform
              </p>
            </div>
          </div>
        </div>

        <nav className="sidebar-nav">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
            >
              <Icon size={18} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        {/* User section */}
        <div style={{
          padding: '1rem 0.75rem',
          borderTop: '1px solid var(--border-subtle)',
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.75rem',
            padding: '0.625rem', borderRadius: 'var(--radius-md)',
            background: 'var(--glass-bg)',
          }}>
            <div style={{
              width: 32, height: 32, borderRadius: '50%',
              background: 'linear-gradient(135deg, #3b82f6, #8b5cf6)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0,
            }}>
              <span style={{ color: '#fff', fontSize: '0.75rem', fontWeight: 600 }}>
                {(user?.name || 'U')[0].toUpperCase()}
              </span>
            </div>
            <div style={{ flex: 1, overflow: 'hidden' }}>
              <p style={{ fontSize: '0.8125rem', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {user?.name || 'User'}
              </p>
              <p style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {user?.username || ''}
              </p>
            </div>
            <button onClick={handleLogout} title="Sign out" style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: 'var(--color-text-muted)', padding: 4, borderRadius: 6,
              transition: 'color 0.15s',
            }}>
              <LogOut size={15} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="main-content">
        {/* Topbar */}
        <header className="topbar">
          <div />
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <button className="btn btn-secondary btn-sm" style={{ gap: '0.375rem' }}>
              <Bell size={14} />
            </button>
            <div style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              padding: '0.375rem 0.875rem',
              background: 'rgba(34,197,94,0.1)',
              border: '1px solid rgba(34,197,94,0.3)',
              borderRadius: 'var(--radius-full)',
            }}>
              <Shield size={13} color="var(--color-success)" />
              <span style={{ fontSize: '0.75rem', color: 'var(--color-success)', fontWeight: 500 }}>
                Azure AD
              </span>
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="page-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
