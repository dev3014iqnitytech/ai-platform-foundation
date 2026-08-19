import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Upload, BookOpen, Trash2 } from 'lucide-react';
import { knowledgeApi } from '@/services/api.client';

const CATEGORIES = [
  'testing_standards','org_guidelines','existing_test_cases',
  'domain_documents','business_rules','test_templates',
  'regulatory_documents','qa_checklists','naming_standards',
];

export function KnowledgeBasePage() {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState(CATEGORIES[0]);

  const { data: docs = [], isLoading } = useQuery({
    queryKey: ['kb-docs'],
    queryFn: () => knowledgeApi.listDocuments().then(r => r.data),
  });

  const uploadMutation = useMutation({
    mutationFn: () => knowledgeApi.upload(file!, category).then(r => r.data),
    onSuccess: () => { setFile(null); queryClient.invalidateQueries({ queryKey: ['kb-docs'] }); },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => knowledgeApi.deleteDocument(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['kb-docs'] }),
  });

  return (
    <div>
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ marginBottom: '0.375rem' }}>Knowledge Base</h1>
        <p className="text-muted">Upload testing standards, templates, and guidelines to enrich AI test generation</p>
      </div>

      {/* Upload */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <h3 style={{ marginBottom: '1.25rem' }}>Upload Document</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: '0.75rem', alignItems: 'end' }}>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.875rem', fontWeight: 500 }}>Document</label>
            <input type="file" accept=".pdf,.docx,.md,.txt,.html"
              onChange={e => setFile(e.target.files?.[0] ?? null)}
              style={{ width: '100%', padding: '0.5rem', background: 'rgba(255,255,255,0.06)',
                border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', color: 'var(--color-text-primary)' }} />
          </div>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.875rem', fontWeight: 500 }}>Category</label>
            <select className="input" value={category} onChange={e => setCategory(e.target.value)}>
              {CATEGORIES.map(c => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
            </select>
          </div>
          <button className="btn btn-primary" onClick={() => uploadMutation.mutate()} disabled={!file || uploadMutation.isPending}>
            {uploadMutation.isPending ? <><span className="spinner" style={{ width: 14, height: 14 }} /> Uploading...</> : <><Upload size={15} /> Upload</>}
          </button>
        </div>
      </div>

      {/* Document list */}
      <div className="table-container">
        <table>
          <thead>
            <tr><th>Filename</th><th>Category</th><th>Chunks</th><th>Version</th><th>Uploaded</th><th></th></tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={6} style={{ textAlign: 'center', padding: '2rem' }}><span className="spinner" /></td></tr>
            ) : (docs as any[]).map((doc: any) => (
              <tr key={doc.id}>
                <td><div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}><BookOpen size={14} color="var(--color-brand-400)" />{doc.filename}</div></td>
                <td><span className="badge badge-review">{doc.category?.replace(/_/g, ' ')}</span></td>
                <td className="text-muted">{doc.chunk_count ?? '—'}</td>
                <td className="text-muted">v{doc.version}</td>
                <td className="text-muted text-sm">{new Date(doc.created_at).toLocaleDateString()}</td>
                <td>
                  <button className="btn btn-danger btn-sm" onClick={() => deleteMutation.mutate(doc.id)} disabled={deleteMutation.isPending}>
                    <Trash2 size={12} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
