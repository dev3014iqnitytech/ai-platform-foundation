import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ScrollText, Filter } from 'lucide-react';
import { auditApi } from '@/services/api.client';

const ACTION_COLORS: Record<string, string> = {
  approved: 'badge-approved',
  rejected: 'badge-rejected',
  session_created: 'badge-review',
  story_fetched: 'badge-draft',
  ado_updated: 'badge-published',
  document_uploaded: 'badge-review',
};

export function AuditLogsPage() {
  const [actionFilter, setActionFilter] = useState('');
  const [page, setPage] = useState(1);

  const { data, isLoading } = useQuery({
    queryKey: ['audit-logs', actionFilter, page],
    queryFn: () => auditApi.list({ action: actionFilter || undefined, page, page_size: 50 }).then(r => r.data),
  });

  const logs: any[] = data?.items ?? [];
  const total: number = data?.total ?? 0;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '2rem' }}>
        <div>
          <h1 style={{ marginBottom: '0.375rem' }}>Audit Logs</h1>
          <p className="text-muted">Immutable record of all platform actions for compliance and governance</p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <Filter size={16} color="var(--color-text-muted)" />
          <select className="input" style={{ width: 200 }} value={actionFilter} onChange={e => { setActionFilter(e.target.value); setPage(1); }}>
            <option value="">All Actions</option>
            {['session_created','story_fetched','gherkin_generated','test_cases_generated',
              'approved','rejected','ado_updated','document_uploaded'].map(a => (
              <option key={a} value={a}>{a.replace(/_/g, ' ')}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="card" style={{ marginBottom: '1rem', padding: '0.75rem 1.25rem' }}>
        <p className="text-sm text-muted">
          <ScrollText size={14} style={{ display: 'inline', marginRight: 6, verticalAlign: 'middle' }} />
          Showing {logs.length} of {total.toLocaleString()} total audit events
        </p>
      </div>

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Action</th>
              <th>Actor</th>
              <th>Entity</th>
              <th>Session</th>
              <th>IP Address</th>
              <th>Timestamp</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={6} style={{ textAlign: 'center', padding: '2rem' }}><span className="spinner" /></td></tr>
            ) : logs.length === 0 ? (
              <tr><td colSpan={6} style={{ textAlign: 'center', padding: '2rem', color: 'var(--color-text-muted)' }}>No audit events found</td></tr>
            ) : logs.map((log: any) => (
              <tr key={log.id}>
                <td>
                  <span className={`badge ${ACTION_COLORS[log.action] || 'badge-draft'}`}>
                    {log.action?.replace(/_/g, ' ')}
                  </span>
                </td>
                <td>
                  <div>
                    <p style={{ fontSize: '0.8125rem', fontWeight: 500 }}>{log.actor_email || '—'}</p>
                    <p className="text-xs text-muted font-mono">{log.actor_id?.slice(0, 8)}...</p>
                  </div>
                </td>
                <td className="text-sm text-muted">{log.entity_type || '—'}</td>
                <td>
                  {log.session_id
                    ? <span className="font-mono text-xs text-accent">{log.session_id.slice(0, 8)}...</span>
                    : <span className="text-muted">—</span>
                  }
                </td>
                <td className="font-mono text-sm text-muted">{log.ip_address || '—'}</td>
                <td className="text-sm text-muted">
                  {new Date(log.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > 50 && (
        <div style={{ display: 'flex', justifyContent: 'center', gap: '0.5rem', marginTop: '1.5rem' }}>
          <button className="btn btn-secondary btn-sm" disabled={page === 1} onClick={() => setPage(p => p - 1)}>← Previous</button>
          <span style={{ display: 'flex', alignItems: 'center', fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>
            Page {page} of {Math.ceil(total / 50)}
          </span>
          <button className="btn btn-secondary btn-sm" disabled={page >= Math.ceil(total / 50)} onClick={() => setPage(p => p + 1)}>Next →</button>
        </div>
      )}
    </div>
  );
}
