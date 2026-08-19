import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { CheckSquare, XCircle, MessageSquare, History, ChevronDown, ChevronUp } from 'lucide-react';
import { approvalsApi, testCasesApi } from '@/services/api.client';

function GherkinBlock({ text }: { text: string }) {
  if (!text) return null;
  const lines = text.split('\n');
  return (
    <div className="gherkin-block">
      {lines.map((line, i) => {
        const trimmed = line.trim();
        const keyword = ['Feature:', 'Scenario:', 'Given ', 'When ', 'Then ', 'And ', 'But ']
          .find(k => trimmed.startsWith(k));
        if (!keyword) return <div key={i}>{line}</div>;
        const color = keyword.startsWith('Feature') ? '#82aaff'
          : keyword.startsWith('Scenario') ? '#ffcb6b'
          : '#a3f7bf';
        return (
          <div key={i}>
            <span style={{ color, fontWeight: 600 }}>{keyword}</span>
            {trimmed.slice(keyword.length)}
          </div>
        );
      })}
    </div>
  );
}

function TestCaseCard({ tc, index }: { tc: any; index: number }) {
  const [open, setOpen] = useState(index < 3);
  const typeColorMap: Record<string, string> = {
    functional: '#93c5fd', negative: '#fca5a5', boundary: '#fcd34d',
    api: '#c4b5fd', ui: '#5eead4', smoke: '#86efac',
    regression: '#fdba74', integration: '#f9a8d4', edge_case: '#6ee7b7',
    error_handling: '#fda4af',
  };
  const color = typeColorMap[tc.type] || '#94a3b8';

  return (
    <div className="card" style={{ padding: '0', overflow: 'hidden' }}>
      <div
        style={{
          padding: '1rem 1.25rem', display: 'flex',
          alignItems: 'center', gap: '1rem', cursor: 'pointer',
        }}
        onClick={() => setOpen(o => !o)}
      >
        <span style={{
          width: 28, height: 28, borderRadius: 8, flexShrink: 0,
          background: `${color}20`, color,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '0.7rem', fontWeight: 700,
        }}>{index + 1}</span>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
            <span className={`badge badge-${tc.type?.replace('_', '-') || 'functional'}`}>{tc.type}</span>
            <span className={`badge badge-${tc.priority}`}>{tc.priority}</span>
            {tc.is_smoke_candidate && <span className="badge badge-smoke">Smoke</span>}
            {tc.is_regression_candidate && <span className="badge badge-regression">Regression</span>}
          </div>
          <p style={{ marginTop: '0.375rem', fontWeight: 500, fontSize: '0.9rem' }}>{tc.title}</p>
        </div>
        {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </div>

      {open && (
        <div style={{ padding: '0 1.25rem 1.25rem', borderTop: '1px solid var(--border-subtle)' }}>
          {tc.preconditions && (
            <div style={{ marginTop: '1rem' }}>
              <p className="text-xs text-muted" style={{ marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Preconditions</p>
              <p className="text-sm">{tc.preconditions}</p>
            </div>
          )}
          {tc.gherkin_text && (
            <div style={{ marginTop: '1rem' }}>
              <p className="text-xs text-muted" style={{ marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Gherkin</p>
              <GherkinBlock text={tc.gherkin_text} />
            </div>
          )}
          {tc.steps?.length > 0 && (
            <div style={{ marginTop: '1rem' }}>
              <p className="text-xs text-muted" style={{ marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Steps</p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {tc.steps.map((step: any) => (
                  <div key={step.step_number} style={{
                    display: 'grid', gridTemplateColumns: '28px 1fr 1fr',
                    gap: '0.75rem', alignItems: 'start', fontSize: '0.8125rem',
                  }}>
                    <span style={{
                      width: 22, height: 22, borderRadius: '50%', background: 'rgba(59,130,246,0.2)',
                      color: 'var(--color-brand-400)', display: 'flex',
                      alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', fontWeight: 700,
                    }}>{step.step_number}</span>
                    <span>{step.action}</span>
                    <span className="text-muted">{step.expected_result}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ReviewPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [comment, setComment] = useState('');
  const [activeTab, setActiveTab] = useState<'cases' | 'comments' | 'history'>('cases');

  const { data: testCases = [] } = useQuery({
    queryKey: ['test-cases', sessionId],
    queryFn: () => testCasesApi.getBySession(sessionId!).then(r => r.data),
    enabled: !!sessionId,
  });

  const { data: comments = [] } = useQuery({
    queryKey: ['comments', sessionId],
    queryFn: () => approvalsApi.getComments(sessionId!).then(r => r.data),
    enabled: !!sessionId && activeTab === 'comments',
  });

  const { data: history = [] } = useQuery({
    queryKey: ['history', sessionId],
    queryFn: () => approvalsApi.getHistory(sessionId!).then(r => r.data),
    enabled: !!sessionId && activeTab === 'history',
  });

  const reviewMutation = useMutation({
    mutationFn: ({ action, c }: { action: 'approve' | 'reject'; c?: string }) =>
      approvalsApi.review(sessionId!, action, c).then(r => r.data),
    onSuccess: (_, { action }) => {
      queryClient.invalidateQueries({ queryKey: ['approval-queue'] });
      navigate('/approvals');
    },
  });

  const commentMutation = useMutation({
    mutationFn: (text: string) => approvalsApi.addComment(sessionId!, text).then(r => r.data),
    onSuccess: () => {
      setComment('');
      queryClient.invalidateQueries({ queryKey: ['comments', sessionId] });
    },
  });

  const tabs = [
    { id: 'cases',    label: `Test Cases (${testCases.length})`, icon: CheckSquare },
    { id: 'comments', label: 'Comments', icon: MessageSquare },
    { id: 'history',  label: 'Audit History', icon: History },
  ] as const;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '2rem' }}>
        <div>
          <button onClick={() => navigate('/approvals')} className="btn btn-secondary btn-sm" style={{ marginBottom: '0.75rem' }}>
            ← Back to Queue
          </button>
          <h1>Review Test Cases</h1>
          <p className="text-muted font-mono" style={{ marginTop: '0.375rem' }}>Session: {sessionId?.slice(0, 8)}...</p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button
            className="btn btn-danger"
            onClick={() => {
              const c = prompt('Rejection reason (required):');
              if (c) reviewMutation.mutate({ action: 'reject', c });
            }}
            disabled={reviewMutation.isPending}
          >
            <XCircle size={16} /> Reject
          </button>
          <button
            className="btn btn-success"
            onClick={() => reviewMutation.mutate({ action: 'approve' })}
            disabled={reviewMutation.isPending}
          >
            <CheckSquare size={16} /> Approve & Publish to ADO
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '1.5rem', borderBottom: '1px solid var(--border-subtle)', paddingBottom: '0' }}>
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            style={{
              padding: '0.625rem 1.25rem', background: 'none', border: 'none',
              cursor: 'pointer', fontSize: '0.875rem', fontWeight: 500,
              color: activeTab === id ? 'var(--color-brand-400)' : 'var(--color-text-muted)',
              borderBottom: `2px solid ${activeTab === id ? 'var(--color-brand-500)' : 'transparent'}`,
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              transition: 'all 0.15s', marginBottom: -1,
            }}
          >
            <Icon size={15} /> {label}
          </button>
        ))}
      </div>

      {/* Test Cases Tab */}
      {activeTab === 'cases' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {testCases.map((tc: any, i: number) => (
            <TestCaseCard key={tc.id} tc={tc} index={i} />
          ))}
        </div>
      )}

      {/* Comments Tab */}
      {activeTab === 'comments' && (
        <div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1.5rem' }}>
            {(comments as any[]).map((c: any) => (
              <div key={c.id} className="card" style={{ padding: '1rem' }}>
                <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
                  <span style={{ fontWeight: 600, fontSize: '0.875rem' }}>{c.author_name}</span>
                  <span className="text-muted text-sm">{new Date(c.created_at).toLocaleString()}</span>
                </div>
                <p style={{ fontSize: '0.875rem' }}>{c.comment}</p>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <input
              className="input"
              placeholder="Add a review comment..."
              value={comment}
              onChange={e => setComment(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && comment.trim() && commentMutation.mutate(comment.trim())}
            />
            <button
              className="btn btn-primary"
              onClick={() => comment.trim() && commentMutation.mutate(comment.trim())}
              disabled={!comment.trim() || commentMutation.isPending}
            >
              <MessageSquare size={15} /> Post
            </button>
          </div>
        </div>
      )}

      {/* History Tab */}
      {activeTab === 'history' && (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Action</th><th>Actor</th><th>From</th><th>To</th><th>Comment</th><th>Date</th>
              </tr>
            </thead>
            <tbody>
              {(history as any[]).map((h: any) => (
                <tr key={h.id}>
                  <td><span className={`badge ${h.action === 'approved' ? 'badge-approved' : h.action === 'rejected' ? 'badge-rejected' : 'badge-review'}`}>{h.action}</span></td>
                  <td>{h.actor_name}</td>
                  <td><span className="badge badge-draft">{h.previous_status}</span></td>
                  <td><span className={`badge ${h.new_status === 'APPROVED' ? 'badge-approved' : h.new_status === 'REJECTED' ? 'badge-rejected' : 'badge-review'}`}>{h.new_status}</span></td>
                  <td className="text-muted text-sm">{h.comment || '—'}</td>
                  <td className="text-muted text-sm">{new Date(h.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
