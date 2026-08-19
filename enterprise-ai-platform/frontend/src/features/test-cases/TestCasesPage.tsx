import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  TestTube, Filter, Download, ChevronRight, CheckCircle,
  AlertTriangle, Code, Layers, Zap, Shield
} from 'lucide-react';
import testCasesService, { type TestCase } from '@/services/testcases.service';

const TYPE_ICONS: Record<string, React.ReactNode> = {
  Functional: <CheckCircle size={13} />,
  Boundary:   <Layers size={13} />,
  API:        <Code size={13} />,
  Security:   <Shield size={13} />,
  Performance:<Zap size={13} />,
  E2E:        <TestTube size={13} />,
};

const PRIORITY_COLORS: Record<string, string> = {
  '1': 'var(--color-danger)',
  '2': 'var(--color-warning)',
  '3': 'var(--color-brand-400)',
  '4': 'var(--color-text-muted)',
};

const PRIORITY_LABELS: Record<string, string> = {
  '1': 'Critical', '2': 'High', '3': 'Medium', '4': 'Low',
};

const TEST_TYPES = ['Functional', 'Boundary', 'API', 'Security', 'Performance', 'E2E'];

export function TestCasesPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [typeFilter, setTypeFilter] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['test-cases', sessionId, typeFilter],
    queryFn: () => testCasesService.listBySession(sessionId!, {
      type: typeFilter || undefined,
      size: 100,
    }),
    enabled: !!sessionId,
  });

  const testCases: TestCase[] = data?.items ?? [];

  if (!sessionId) {
    return (
      <div className="card" style={{ textAlign: 'center', padding: '4rem' }}>
        <TestTube size={48} color="var(--color-brand-400)" style={{ margin: '0 auto 1rem', display: 'block' }} />
        <h2>No Session Selected</h2>
        <p className="text-muted" style={{ marginTop: '0.5rem' }}>
          Generate test cases from Story Search first.
        </p>
        <button className="btn btn-primary" style={{ marginTop: '1.5rem' }}
          onClick={() => navigate('/story-search')}>
          Go to Story Search
        </button>
      </div>
    );
  }

  const handleDownload = async () => {
    await testCasesService.downloadExport(sessionId, 'json');
  };

  const byType = TEST_TYPES.reduce<Record<string, number>>((acc, t) => {
    acc[t] = testCases.filter(tc => tc.type === t).length;
    return acc;
  }, {});

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '2rem' }}>
        <div>
          <h1 style={{ marginBottom: '0.375rem' }}>Generated Test Cases</h1>
          <p className="text-muted font-mono" style={{ fontSize: '0.8rem' }}>
            Session: {sessionId.slice(0, 8)}... · {testCases.length} test cases
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button className="btn btn-secondary" onClick={handleDownload}>
            <Download size={14} /> Export JSON
          </button>
          <button
            className="btn btn-primary"
            onClick={() => navigate(`/approval-queue`)}
          >
            Submit for Review <ChevronRight size={14} />
          </button>
        </div>
      </div>

      {/* Type summary chips */}
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1.5rem' }}>
        <button
          className={`btn btn-sm ${typeFilter === '' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setTypeFilter('')}
        >
          All ({testCases.length})
        </button>
        {TEST_TYPES.filter(t => byType[t] > 0).map(t => (
          <button
            key={t}
            className={`btn btn-sm ${typeFilter === t ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setTypeFilter(t === typeFilter ? '' : t)}
            style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}
          >
            {TYPE_ICONS[t]} {t} ({byType[t]})
          </button>
        ))}
      </div>

      {/* Test case list */}
      {isLoading ? (
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', justifyContent: 'center', padding: '4rem' }}>
          <span className="spinner" /> Loading test cases...
        </div>
      ) : testCases.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
          <AlertTriangle size={36} color="var(--color-warning)" style={{ margin: '0 auto 1rem', display: 'block' }} />
          <p className="text-muted">No test cases match the selected filter.</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {testCases.map((tc) => (
            <div
              key={tc.id}
              className="card"
              style={{ padding: '1.25rem', cursor: 'pointer', transition: 'border-color 0.2s' }}
              onClick={() => setExpandedId(expandedId === tc.id ? null : tc.id)}
            >
              {/* Card header */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.625rem' }}>
                <span style={{
                  padding: '0.2rem 0.6rem', borderRadius: '999px', fontSize: '0.72rem', fontWeight: 600,
                  background: 'rgba(99,102,241,0.15)', color: 'var(--color-brand-400)',
                  display: 'flex', alignItems: 'center', gap: '0.3rem',
                }}>
                  {TYPE_ICONS[tc.type] ?? <TestTube size={13} />} {tc.type}
                </span>
                <span style={{
                  padding: '0.2rem 0.6rem', borderRadius: '999px', fontSize: '0.72rem', fontWeight: 600,
                  background: 'rgba(255,255,255,0.06)', color: PRIORITY_COLORS[tc.priority] ?? 'var(--color-text-muted)',
                }}>
                  P{tc.priority} – {PRIORITY_LABELS[tc.priority] ?? tc.priority}
                </span>
                {tc.ado_test_case_id && (
                  <span style={{
                    padding: '0.2rem 0.6rem', borderRadius: '999px', fontSize: '0.72rem',
                    background: 'rgba(34,197,94,0.12)', color: '#4ade80',
                  }}>
                    ADO #{tc.ado_test_case_id}
                  </span>
                )}
                <span style={{ marginLeft: 'auto', color: 'var(--color-text-muted)', fontSize: '0.8rem' }}>
                  {expandedId === tc.id ? '▲' : '▼'}
                </span>
              </div>

              <h4 style={{ fontWeight: 600, marginBottom: '0.25rem' }}>{tc.title}</h4>

              {tc.tags && tc.tags.length > 0 && (
                <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap', marginTop: '0.5rem' }}>
                  {tc.tags.map(tag => (
                    <span key={tag} style={{
                      fontSize: '0.7rem', padding: '0.15rem 0.5rem', borderRadius: '4px',
                      background: 'rgba(255,255,255,0.06)', color: 'var(--color-text-muted)',
                    }}>#{tag}</span>
                  ))}
                </div>
              )}

              {/* Expanded details */}
              {expandedId === tc.id && (
                <div style={{ marginTop: '1rem', borderTop: '1px solid var(--border-subtle)', paddingTop: '1rem' }}>
                  {tc.gherkin_text ? (
                    <pre style={{
                      fontFamily: 'var(--font-mono)', fontSize: '0.78rem', lineHeight: 1.6,
                      background: 'rgba(0,0,0,0.3)', padding: '1rem', borderRadius: '8px',
                      overflowX: 'auto', color: 'var(--color-text-secondary)',
                    }}>
                      {tc.gherkin_text}
                    </pre>
                  ) : tc.steps && tc.steps.length > 0 ? (
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                          <th style={{ textAlign: 'left', padding: '0.5rem', width: '40px', color: 'var(--color-text-muted)' }}>#</th>
                          <th style={{ textAlign: 'left', padding: '0.5rem', color: 'var(--color-text-muted)' }}>Action</th>
                          <th style={{ textAlign: 'left', padding: '0.5rem', color: 'var(--color-text-muted)' }}>Expected Result</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tc.steps.map((step) => (
                          <tr key={step.step_number} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                            <td style={{ padding: '0.5rem', color: 'var(--color-text-muted)' }}>{step.step_number}</td>
                            <td style={{ padding: '0.5rem' }}>{step.action}</td>
                            <td style={{ padding: '0.5rem', color: 'var(--color-text-secondary)' }}>{step.expected_result}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p className="text-muted" style={{ fontSize: '0.85rem' }}>No steps defined.</p>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
