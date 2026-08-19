import { useQuery } from '@tanstack/react-query';
import { useMsal } from '@azure/msal-react';
import { TestTube, CheckSquare, Clock, TrendingUp, BookOpen, Zap } from 'lucide-react';
import { adminApi } from '@/services/api.client';

function StatCard({ icon: Icon, label, value, color, delta }: {
  icon: React.ElementType; label: string; value: string | number;
  color: string; delta?: string;
}) {
  return (
    <div className="card" style={{ position: 'relative', overflow: 'hidden' }}>
      <div style={{
        position: 'absolute', top: -20, right: -20,
        width: 80, height: 80, borderRadius: '50%',
        background: color, opacity: 0.08,
      }} />
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <p className="text-sm text-muted" style={{ marginBottom: '0.5rem' }}>{label}</p>
          <p style={{ fontSize: '2rem', fontWeight: 700, lineHeight: 1 }}>{value}</p>
          {delta && (
            <p style={{ fontSize: '0.75rem', color: 'var(--color-success)', marginTop: '0.375rem' }}>
              {delta}
            </p>
          )}
        </div>
        <div style={{
          width: 42, height: 42, borderRadius: 10,
          background: color, opacity: 0.15,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Icon size={20} color={color} style={{ opacity: 1 }} />
        </div>
      </div>
    </div>
  );
}

export function DashboardPage() {
  const { accounts } = useMsal();
  const user = accounts[0];
  const { data: stats } = useQuery({
    queryKey: ['admin-stats'],
    queryFn: () => adminApi.getStats().then(r => r.data),
    staleTime: 60_000,
  });

  const greeting = (() => {
    const h = new Date().getHours();
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    return 'Good evening';
  })();

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ marginBottom: '0.375rem' }}>
          {greeting}, {user?.name?.split(' ')[0] || 'User'} 👋
        </h1>
        <p className="text-muted">
          Here's what's happening on the platform today.
        </p>
      </div>

      {/* KPI Cards */}
      <div className="grid-4" style={{ marginBottom: '2rem' }}>
        <StatCard
          icon={TestTube}
          label="Test Cases Generated"
          value={stats?.test_cases_generated?.toLocaleString() ?? '—'}
          color="#3b82f6"
          delta="↑ 12% this week"
        />
        <StatCard
          icon={Clock}
          label="Pending Reviews"
          value="—"
          color="#f59e0b"
        />
        <StatCard
          icon={CheckSquare}
          label="Approved Sessions"
          value={stats?.generation_sessions?.toLocaleString() ?? '—'}
          color="#22c55e"
        />
        <StatCard
          icon={BookOpen}
          label="Knowledge Docs"
          value={stats?.knowledge_documents?.toLocaleString() ?? '—'}
          color="#8b5cf6"
        />
      </div>

      {/* Quick Actions */}
      <div style={{ marginBottom: '2rem' }}>
        <h3 style={{ marginBottom: '1rem' }}>Quick Actions</h3>
        <div className="grid-3">
          {[
            {
              icon: Zap, title: 'Generate Test Cases',
              desc: 'Enter a User Story ID to start AI generation',
              href: '/stories', color: '#3b82f6',
            },
            {
              icon: CheckSquare, title: 'Review Queue',
              desc: 'Review and approve AI-generated test cases',
              href: '/approvals', color: '#22c55e',
            },
            {
              icon: BookOpen, title: 'Upload Standards',
              desc: 'Add testing standards to the knowledge base',
              href: '/knowledge', color: '#8b5cf6',
            },
          ].map(({ icon: Icon, title, desc, href, color }) => (
            <a key={href} href={href} className="card" style={{
              display: 'block', textDecoration: 'none',
              transition: 'all 0.2s ease', cursor: 'pointer',
            }}
              onMouseEnter={e => (e.currentTarget.style.transform = 'translateY(-2px)')}
              onMouseLeave={e => (e.currentTarget.style.transform = 'none')}
            >
              <div style={{
                width: 44, height: 44, borderRadius: 12,
                background: `${color}20`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginBottom: '1rem',
              }}>
                <Icon size={22} color={color} />
              </div>
              <h4 style={{ marginBottom: '0.375rem' }}>{title}</h4>
              <p className="text-sm text-muted">{desc}</p>
            </a>
          ))}
        </div>
      </div>

      {/* Platform Stats */}
      <div className="card">
        <h3 style={{ marginBottom: '1.25rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <TrendingUp size={18} color="var(--color-brand-400)" />
          Platform Overview
        </h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '1.25rem' }}>
          {[
            { label: 'Total Users', value: stats?.users ?? '—' },
            { label: 'Generation Sessions', value: stats?.generation_sessions ?? '—' },
            { label: 'Test Cases Created', value: stats?.test_cases_generated ?? '—' },
            { label: 'Knowledge Documents', value: stats?.knowledge_documents ?? '—' },
          ].map(({ label, value }) => (
            <div key={label} style={{
              padding: '1rem', borderRadius: 'var(--radius-md)',
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid var(--border-subtle)',
            }}>
              <p className="text-xs text-muted" style={{ marginBottom: '0.375rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</p>
              <p style={{ fontSize: '1.5rem', fontWeight: 700 }}>{typeof value === 'number' ? value.toLocaleString() : value}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
