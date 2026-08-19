import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Search, Zap, Tag, GitBranch, AlertCircle, CheckCircle, Loader } from 'lucide-react';
import { storiesApi } from '@/services/api.client';

export function StorySearchPage() {
  const [storyId, setStoryId] = useState('');
  const [fetchedStory, setFetchedStory] = useState<any>(null);
  const navigate = useNavigate();

  // Fetch story preview
  const fetchMutation = useMutation({
    mutationFn: (id: string) => storiesApi.fetchStory(id).then(r => r.data),
    onSuccess: (data) => setFetchedStory(data),
  });

  // Generate test cases
  const generateMutation = useMutation({
    mutationFn: () => storiesApi.generateTestCases({
      user_story_id: storyId,
      max_test_cases: 30,
    }).then(r => r.data),
    onSuccess: (data) => navigate(`/test-cases/${data.id}`),
  });

  const handleFetch = (e: React.FormEvent) => {
    e.preventDefault();
    if (storyId.trim()) fetchMutation.mutate(storyId.trim());
  };

  return (
    <div>
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ marginBottom: '0.375rem' }}>Story Search</h1>
        <p className="text-muted">Enter an Azure DevOps User Story ID to generate AI test cases</p>
      </div>

      {/* Search Form */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <form onSubmit={handleFetch} style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.875rem', fontWeight: 500 }}>
              User Story ID
            </label>
            <input
              className="input"
              type="text"
              placeholder="e.g. US-12345 or 12345"
              value={storyId}
              onChange={e => setStoryId(e.target.value)}
              pattern="[A-Za-z0-9\-]+"
              required
            />
          </div>
          <button
            type="submit"
            className="btn btn-secondary"
            disabled={fetchMutation.isPending}
            style={{ flexShrink: 0 }}
          >
            {fetchMutation.isPending
              ? <><span className="spinner" style={{ width: 14, height: 14 }} /> Fetching...</>
              : <><Search size={16} /> Fetch Story</>
            }
          </button>
        </form>

        {fetchMutation.isError && (
          <div style={{
            marginTop: '1rem', padding: '0.875rem',
            background: 'var(--color-error-bg)', borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(239,68,68,0.3)',
            display: 'flex', gap: '0.5rem', alignItems: 'center',
            color: 'var(--color-error)', fontSize: '0.875rem',
          }}>
            <AlertCircle size={16} /> Failed to fetch story. Verify the ID and ADO connection.
          </div>
        )}
      </div>

      {/* Story Preview */}
      {fetchedStory && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '1.25rem' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
                <span className="badge badge-review">{fetchedStory.work_item_type || 'User Story'}</span>
                <span className="font-mono text-muted text-sm">#{fetchedStory.id}</span>
              </div>
              <h2 style={{ fontSize: '1.25rem' }}>{fetchedStory.title}</h2>
            </div>
            <span style={{
              padding: '0.25rem 0.75rem', borderRadius: 'var(--radius-full)',
              fontSize: '0.75rem', fontWeight: 600,
              background: 'var(--color-success-bg)', color: 'var(--color-success)',
              display: 'flex', alignItems: 'center', gap: '0.375rem',
            }}>
              <CheckCircle size={12} /> {fetchedStory.state || 'Active'}
            </span>
          </div>

          {fetchedStory.area_path && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
              <GitBranch size={14} color="var(--color-text-muted)" />
              <span className="text-sm text-muted">{fetchedStory.area_path}</span>
            </div>
          )}

          {fetchedStory.tags?.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.375rem', marginBottom: '1rem' }}>
              <Tag size={14} color="var(--color-text-muted)" />
              {fetchedStory.tags.map((tag: string) => (
                <span key={tag} className="badge" style={{
                  background: 'rgba(139,92,246,0.15)', color: '#c4b5fd',
                }}>{tag}</span>
              ))}
            </div>
          )}

          {fetchedStory.acceptance_criteria && (
            <div>
              <p style={{ fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-muted)', marginBottom: '0.625rem' }}>
                Acceptance Criteria
              </p>
              <div style={{
                padding: '1rem', background: 'rgba(0,0,0,0.3)',
                borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)',
                fontSize: '0.875rem', lineHeight: 1.7,
                whiteSpace: 'pre-wrap', maxHeight: 200, overflowY: 'auto',
              }}>
                {fetchedStory.acceptance_criteria}
              </div>
            </div>
          )}

          <div style={{ marginTop: '1.5rem', display: 'flex', gap: '0.75rem' }}>
            <button
              className="btn btn-primary"
              onClick={() => generateMutation.mutate()}
              disabled={generateMutation.isPending}
              style={{ flex: 1, justifyContent: 'center' }}
            >
              {generateMutation.isPending
                ? <><span className="spinner" style={{ width: 16, height: 16 }} /> Generating AI Test Cases...</>
                : <><Zap size={16} /> Generate Test Cases with AI</>
              }
            </button>
          </div>

          {generateMutation.isPending && (
            <p style={{ textAlign: 'center', fontSize: '0.8rem', color: 'var(--color-text-muted)', marginTop: '0.75rem' }}>
              Running AI agents: ADO Reader → Gherkin Analyzer → RAG Enrichment → Test Creation...
            </p>
          )}
        </div>
      )}
    </div>
  );
}
