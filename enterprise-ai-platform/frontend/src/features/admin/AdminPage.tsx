import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Users, Settings, BarChart3, Shield } from 'lucide-react';
import { adminApi } from '@/services/api.client';

const ALL_ROLES = ['system_admin','qa_manager','senior_tester','tester','developer','approver','architect','read_only'];

export function AdminPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<'users' | 'settings' | 'stats'>('users');
  const [editingUser, setEditingUser] = useState<any>(null);
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);

  const { data: users = [] } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => adminApi.listUsers().then(r => r.data),
    enabled: activeTab === 'users',
  });

  const { data: settings } = useQuery({
    queryKey: ['admin-settings'],
    queryFn: () => adminApi.getSettings().then(r => r.data),
    enabled: activeTab === 'settings',
  });

  const { data: stats } = useQuery({
    queryKey: ['admin-stats'],
    queryFn: () => adminApi.getStats().then(r => r.data),
    enabled: activeTab === 'stats',
  });

  const updateRolesMutation = useMutation({
    mutationFn: ({ userId, roles }: { userId: string; roles: string[] }) =>
      adminApi.updateRoles(userId, roles).then(r => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] });
      setEditingUser(null);
    },
  });

  const tabs = [
    { id: 'users',    label: 'User Management', icon: Users    },
    { id: 'settings', label: 'Platform Settings', icon: Settings },
    { id: 'stats',    label: 'Statistics',        icon: BarChart3},
  ] as const;

  return (
    <div>
      <div style={{ marginBottom: '2rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.375rem' }}>
          <Shield size={24} color="var(--color-brand-400)" />
          <h1>Admin Console</h1>
        </div>
        <p className="text-muted">Manage users, roles, and platform configuration</p>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '0.25rem', borderBottom: '1px solid var(--border-subtle)', marginBottom: '1.5rem' }}>
        {tabs.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => setActiveTab(id)} style={{
            padding: '0.625rem 1.25rem', background: 'none', border: 'none', cursor: 'pointer',
            fontSize: '0.875rem', fontWeight: 500, marginBottom: -1,
            color: activeTab === id ? 'var(--color-brand-400)' : 'var(--color-text-muted)',
            borderBottom: `2px solid ${activeTab === id ? 'var(--color-brand-500)' : 'transparent'}`,
            display: 'flex', alignItems: 'center', gap: '0.5rem',
          }}>
            <Icon size={15} /> {label}
          </button>
        ))}
      </div>

      {/* Users Tab */}
      {activeTab === 'users' && (
        <div className="table-container">
          <table>
            <thead>
              <tr><th>User</th><th>Email</th><th>Roles</th><th>Status</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {(users as any[]).map((u: any) => (
                <tr key={u.id}>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                      <div style={{
                        width: 32, height: 32, borderRadius: '50%',
                        background: 'linear-gradient(135deg, #3b82f6, #8b5cf6)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: '0.75rem', fontWeight: 700, color: '#fff', flexShrink: 0,
                      }}>{(u.display_name || u.email || 'U')[0].toUpperCase()}</div>
                      <span style={{ fontWeight: 500, fontSize: '0.875rem' }}>{u.display_name}</span>
                    </div>
                  </td>
                  <td className="text-sm text-muted">{u.email}</td>
                  <td>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
                      {(u.roles || []).map((r: string) => (
                        <span key={r} className="badge badge-review" style={{ fontSize: '0.65rem' }}>{r}</span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <span className={`badge ${u.is_active ? 'badge-approved' : 'badge-rejected'}`}>
                      {u.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td>
                    <button className="btn btn-secondary btn-sm" onClick={() => { setEditingUser(u); setSelectedRoles(u.roles || []); }}>
                      Edit Roles
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Role edit modal */}
      {editingUser && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200,
        }} onClick={() => setEditingUser(null)}>
          <div className="card" style={{ width: 440, padding: '2rem' }} onClick={e => e.stopPropagation()}>
            <h3 style={{ marginBottom: '1.25rem' }}>Edit Roles: {editingUser.display_name}</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginBottom: '1.5rem' }}>
              {ALL_ROLES.map(role => (
                <label key={role} style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', cursor: 'pointer', padding: '0.5rem', borderRadius: 'var(--radius-md)', background: selectedRoles.includes(role) ? 'rgba(59,130,246,0.1)' : 'transparent' }}>
                  <input type="checkbox" checked={selectedRoles.includes(role)}
                    onChange={e => setSelectedRoles(prev => e.target.checked ? [...prev, role] : prev.filter(r => r !== role))} />
                  <span style={{ fontSize: '0.875rem', fontWeight: 500 }}>{role.replace(/_/g, ' ')}</span>
                </label>
              ))}
            </div>
            <div style={{ display: 'flex', gap: '0.75rem' }}>
              <button className="btn btn-secondary" style={{ flex: 1 }} onClick={() => setEditingUser(null)}>Cancel</button>
              <button className="btn btn-primary" style={{ flex: 1 }}
                onClick={() => updateRolesMutation.mutate({ userId: editingUser.id, roles: selectedRoles })}
                disabled={updateRolesMutation.isPending}>
                {updateRolesMutation.isPending ? 'Saving...' : 'Save Roles'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Settings Tab */}
      {activeTab === 'settings' && settings && (
        <div className="grid-2">
          {Object.entries(settings).map(([key, value]) => (
            <div key={key} className="card" style={{ padding: '1.25rem' }}>
              <p className="text-xs text-muted" style={{ textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem' }}>
                {key.replace(/_/g, ' ')}
              </p>
              <p style={{ fontSize: '1.125rem', fontWeight: 600, fontFamily: typeof value === 'boolean' ? 'inherit' : 'JetBrains Mono, monospace' }}>
                {typeof value === 'boolean' ? (value ? '✅ Enabled' : '❌ Disabled') : String(value)}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Stats Tab */}
      {activeTab === 'stats' && stats && (
        <div className="grid-2">
          {Object.entries(stats).map(([key, value]) => (
            <div key={key} className="card" style={{ padding: '1.5rem' }}>
              <p className="text-xs text-muted" style={{ textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem' }}>
                {key.replace(/_/g, ' ')}
              </p>
              <p style={{ fontSize: '2.25rem', fontWeight: 700 }}>
                {typeof value === 'number' ? value.toLocaleString() : String(value)}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
