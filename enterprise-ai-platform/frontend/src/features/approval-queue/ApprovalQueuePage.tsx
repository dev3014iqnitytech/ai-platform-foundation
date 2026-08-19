import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { CheckSquare, XCircle, Eye, Clock, RefreshCw } from 'lucide-react';
import { approvalsApi } from '@/services/api.client';

const statusClass: Record<string, string> = {
  DRAFT: 'badge-draft',
  IN_REVIEW: 'badge-review',
  APPROVED: 'badge-approved',
  REJECTED: 'badge-rejected',
  PUBLISHED: 'badge-published',
};

export function ApprovalQueuePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['approval-queue'],
    queryFn: () => approvalsApi.getQueue().then(r => r.data),
    refetchInterval: 15_000, // Poll every 15s for new items
  });

  const reviewMutation = useMutation({
    mutationFn: ({ sessionId, action, comment }: {
      sessionId: string; action: 'approve' | 'reject'; comment?: string;
    }) => approvalsApi.review(sessionId, action, comment).then(r => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['approval-queue'] });
      setSelected(new Set());
    },
  });

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const items: any[] = data ?? [];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '2rem' }}>
        <div>
          <h1 style={{ marginBottom: '0.375rem' }}>Approval Queue</h1>
          <p className="text-muted">Review and approve AI-generated test cases before publishing to Azure DevOps</p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button className="btn btn-secondary btn-sm" onClick={() => refetch()}>
            <RefreshCw size={14} /> Refresh
          </button>
          {selected.size > 0 && (
            <>
              <button
                className="btn btn-success btn-sm"
                onClick={() => selected.forEach(id =>
                  reviewMutation.mutate({ sessionId: id, action: 'approve' })
                )}
                disabled={reviewMutation.isPending}
              >
                <CheckSquare size={14} /> Approve All ({selected.size})
              </button>
              <button
                className="btn btn-danger btn-sm"
                onClick={() => {
                  const comment = prompt('Rejection reason (required):');
                  if (comment) selected.forEach(id =>
                    reviewMutation.mutate({ sessionId: id, action: 'reject', comment })
                  );
                }}
                disabled={reviewMutation.isPending}
              >
                <XCircle size={14} /> Reject All ({selected.size})
              </button>
            </>
          )}
        </div>
      </div>

      {isLoading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '3rem', justifyContent: 'center' }}>
          <span className="spinner" /> Loading queue...
        </div>
      ) : items.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '4rem' }}>
          <CheckSquare size={48} color="var(--color-success)" style={{ margin: '0 auto 1rem', display: 'block' }} />
          <h3 style={{ marginBottom: '0.5rem' }}>Queue is clear!</h3>
          <p className="text-muted">No test cases awaiting review. Generate new ones from Story Search.</p>
        </div>
      ) : (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th style={{ width: 40 }}>
                  <input type="checkbox" onChange={e => {
                    setSelected(e.target.checked ? new Set(items.map(i => i.session_id)) : new Set());
                  }} />
                </th>
                <th>User Story</th>
                <th>Project</th>
                <th>Test Cases</th>
                <th>Revisions</th>
                <th>Status</th>
                <th>Created By</th>
                <th>Date</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item: any) => (
                <tr key={item.session_id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(item.session_id)}
                      onChange={() => toggleSelect(item.session_id)}
                    />
                  </td>
                  <td>
                    <span className="font-mono text-accent" style={{ fontSize: '0.8rem' }}>
                      {item.user_story_id}
                    </span>
                  </td>
                  <td className="text-muted">{item.project_key}</td>
                  <td>
                    <span style={{
                      background: 'rgba(59,130,246,0.15)', color: 'var(--color-brand-400)',
                      padding: '0.125rem 0.5rem', borderRadius: 'var(--radius-full)',
                      fontSize: '0.8rem', fontWeight: 600,
                    }}>
                      {item.test_case_count}
                    </span>
                  </td>
                  <td className="text-muted" style={{ textAlign: 'center' }}>{item.revision_count}</td>
                  <td>
                    <span className={`badge ${statusClass[item.status] || 'badge-draft'}`}>
                      {item.status}
                    </span>
                  </td>
                  <td className="text-sm">{item.created_by_name}</td>
                  <td className="text-muted text-sm">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                      <Clock size={12} />
                      {new Date(item.updated_at).toLocaleDateString()}
                    </div>
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => navigate(`/review/${item.session_id}`)}
                      >
                        <Eye size={13} /> Review
                      </button>
                      <button
                        className="btn btn-success btn-sm"
                        onClick={() => reviewMutation.mutate({ sessionId: item.session_id, action: 'approve' })}
                        disabled={reviewMutation.isPending}
                      >
                        <CheckSquare size={13} />
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => {
                          const comment = prompt('Rejection reason (required):');
                          if (comment) reviewMutation.mutate({ sessionId: item.session_id, action: 'reject', comment });
                        }}
                        disabled={reviewMutation.isPending}
                      >
                        <XCircle size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
