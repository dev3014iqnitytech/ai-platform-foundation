import { useState } from 'react';
import { useMsal } from '@azure/msal-react';
import { Shield, TestTube, Zap, Lock } from 'lucide-react';
import { apiScopes } from './msal.config';
import { LOCAL_TOKEN_KEY } from '@/services/api.client';
import axios from 'axios';

const LOCAL_MODE = import.meta.env.VITE_LOCAL_MODE === 'true';
const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

export function LoginPage() {
  const { instance } = useMsal();
  const [localUser, setLocalUser] = useState('admin');
  const [localRoles, setLocalRoles] = useState('system_admin');
  const [localError, setLocalError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = () => {
    instance.loginRedirect({
      scopes: apiScopes.read,
      prompt: 'select_account',
    });
  };

  const handleLocalLogin = async () => {
    setLoading(true);
    setLocalError('');
    try {
      const roles = localRoles.split(',').map(r => r.trim()).filter(Boolean);
      const res = await axios.post(`${API_BASE}/api/v1/auth/local-token`, {
        username: localUser,
        roles,
      });
      localStorage.setItem(LOCAL_TOKEN_KEY, res.data.access_token);
      window.location.href = '/';
    } catch (e: any) {
      setLocalError(e?.response?.data?.detail || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '2rem',
    }}>
      <div style={{ width: '100%', maxWidth: 440 }}>
        {/* Logo */}
        <div style={{ textAlign: 'center', marginBottom: '3rem' }}>
          <div style={{
            width: 72, height: 72, borderRadius: 20,
            background: 'linear-gradient(135deg, #3b82f6, #6366f1)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '0 auto 1.5rem',
            boxShadow: '0 0 40px rgba(59,130,246,0.4)',
          }}>
            <TestTube size={36} color="white" />
          </div>
          <h1 style={{
            fontSize: '1.75rem', fontWeight: 700,
            background: 'linear-gradient(135deg, #f1f5f9, #94a3b8)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
          }}>
            EATAP
          </h1>
          <p style={{ color: 'var(--color-text-muted)', marginTop: '0.5rem', fontSize: '0.9rem' }}>
            Enterprise AI Test Automation Platform
          </p>
        </div>

        {/* Login card */}
        <div className="card" style={{ padding: '2.5rem', textAlign: 'center' }}>
          <h2 style={{ marginBottom: '0.5rem' }}>Welcome back</h2>

          {LOCAL_MODE ? (
            /* ── Local dev login ── */
            <>
              <p style={{ color: 'var(--color-text-muted)', marginBottom: '1.5rem', fontSize: '0.875rem' }}>
                🛠️ Local development mode — no Azure AD required
              </p>
              <div style={{ textAlign: 'left', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                <label style={{ fontSize: '0.8rem', color: 'var(--color-text-muted)' }}>
                  Username
                  <input
                    value={localUser}
                    onChange={e => setLocalUser(e.target.value)}
                    style={{ display: 'block', width: '100%', marginTop: 4, padding: '0.5rem 0.75rem',
                      borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)',
                      background: 'rgba(255,255,255,0.05)', color: 'inherit', fontSize: '0.9rem' }}
                  />
                </label>
                <label style={{ fontSize: '0.8rem', color: 'var(--color-text-muted)' }}>
                  Roles (comma-separated)
                  <input
                    value={localRoles}
                    onChange={e => setLocalRoles(e.target.value)}
                    placeholder="system_admin, qa_manager, tester…"
                    style={{ display: 'block', width: '100%', marginTop: 4, padding: '0.5rem 0.75rem',
                      borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)',
                      background: 'rgba(255,255,255,0.05)', color: 'inherit', fontSize: '0.9rem' }}
                  />
                </label>
              </div>
              {localError && (
                <p style={{ color: '#f87171', fontSize: '0.8rem', marginTop: '0.5rem' }}>{localError}</p>
              )}
              <button className="btn btn-primary" disabled={loading} style={{
                width: '100%', padding: '0.875rem', fontSize: '0.9375rem',
                justifyContent: 'center', marginTop: '1.25rem',
              }} onClick={handleLocalLogin}>
                {loading ? 'Signing in…' : 'Sign in (Local)'}
              </button>
            </>
          ) : (
            /* ── Azure AD login ── */
            <>
              <p style={{ color: 'var(--color-text-muted)', marginBottom: '2rem', fontSize: '0.875rem' }}>
                Sign in with your enterprise Azure AD account to continue
              </p>
              <button className="btn btn-primary" style={{
                width: '100%', padding: '0.875rem', fontSize: '0.9375rem',
                justifyContent: 'center', gap: '0.75rem',
              }} onClick={handleLogin}>
                <svg width="20" height="20" viewBox="0 0 21 21">
                  <rect x="1" y="1" width="9" height="9" fill="#f25022"/>
                  <rect x="11" y="1" width="9" height="9" fill="#7fba00"/>
                  <rect x="1" y="11" width="9" height="9" fill="#00a4ef"/>
                  <rect x="11" y="11" width="9" height="9" fill="#ffb900"/>
                </svg>
                Sign in with Microsoft
              </button>
            </>
          )}

          {/* Trust badges */}
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
            gap: '0.75rem', marginTop: '2rem',
          }}>
            {[
              { icon: Shield, label: 'Azure AD SSO' },
              { icon: Lock,   label: 'MFA Ready'    },
              { icon: Zap,    label: 'OIDC + PKCE'  },
            ].map(({ icon: Icon, label }) => (
              <div key={label} style={{
                padding: '0.75rem 0.5rem', borderRadius: 'var(--radius-md)',
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid var(--border-subtle)',
              }}>
                <Icon size={16} color="var(--color-brand-400)" style={{ margin: '0 auto 0.375rem', display: 'block' }} />
                <p style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', textAlign: 'center' }}>{label}</p>
              </div>
            ))}
          </div>
        </div>

        <p style={{ textAlign: 'center', marginTop: '1.5rem', fontSize: '0.75rem', color: 'var(--color-text-muted)' }}>
          {LOCAL_MODE ? 'Running in local development mode.' : 'Enterprise authentication only. Contact your IT administrator for access.'}
        </p>
      </div>
    </div>
  );
}
