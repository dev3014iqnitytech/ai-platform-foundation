/**
 * Knowledge Base Service — API client for document management and RAG search
 */
import { apiClient } from './api.client';

export interface KBDocument {
  id: string;
  filename: string;
  category: string;
  version: number;
  chunk_count: number;
  embedding_model: string;
  uploaded_by: string;
  is_active: boolean;
  file_size_bytes: number;
  mime_type: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface KBListResponse {
  items: KBDocument[];
  total: number;
  page: number;
  size: number;
}

export interface SearchRequest {
  query: string;
  top_k?: number;
  category?: string;
  filters?: Record<string, unknown>;
  include_scores?: boolean;
}

export interface SearchResult {
  document_id: string;
  filename: string;
  category: string;
  chunk_text: string;
  relevance_score: number;
  rerank_score?: number;
  metadata: Record<string, unknown>;
}

export interface UploadProgress {
  loaded: number;
  total: number;
  percentage: number;
}

const knowledgeService = {
  /**
   * List all knowledge base documents
   */
  async listDocuments(params?: {
    page?: number;
    size?: number;
    category?: string;
    is_active?: boolean;
  }): Promise<KBListResponse> {
    const response = await apiClient.get<KBListResponse>('/knowledge/documents', { params });
    return response.data;
  },

  /**
   * Get a single document by ID
   */
  async getDocument(documentId: string): Promise<KBDocument> {
    const response = await apiClient.get<KBDocument>(`/knowledge/documents/${documentId}`);
    return response.data;
  },

  /**
   * Upload a document to the knowledge base with progress tracking
   */
  async uploadDocument(
    file: File,
    metadata: { category: string; description?: string },
    onProgress?: (progress: UploadProgress) => void
  ): Promise<KBDocument> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('category', metadata.category);
    if (metadata.description) {
      formData.append('description', metadata.description);
    }

    const response = await apiClient.post<KBDocument>('/knowledge/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (progressEvent) => {
        if (onProgress && progressEvent.total) {
          onProgress({
            loaded: progressEvent.loaded,
            total: progressEvent.total,
            percentage: Math.round((progressEvent.loaded / progressEvent.total) * 100),
          });
        }
      },
    });
    return response.data;
  },

  /**
   * Deactivate (soft-delete) a document
   */
  async deactivateDocument(documentId: string): Promise<void> {
    await apiClient.delete(`/knowledge/documents/${documentId}`);
  },

  /**
   * Re-index a document (re-embed and update vector store)
   */
  async reindexDocument(documentId: string): Promise<{ task_id: string }> {
    const response = await apiClient.post(`/knowledge/documents/${documentId}/reindex`);
    return response.data;
  },

  /**
   * Semantic search across the knowledge base
   */
  async search(request: SearchRequest): Promise<SearchResult[]> {
    const response = await apiClient.post<SearchResult[]>('/knowledge/search', request);
    return response.data;
  },

  /**
   * Get available document categories
   */
  async getCategories(): Promise<string[]> {
    const response = await apiClient.get<string[]>('/knowledge/categories');
    return response.data;
  },

  /**
   * Get knowledge base stats
   */
  async getStats(): Promise<{
    total_documents: number;
    total_chunks: number;
    by_category: Record<string, number>;
    last_updated: string;
    index_health: string;
  }> {
    const response = await apiClient.get('/knowledge/stats');
    return response.data;
  },

  /**
   * Get ingestion task status
   */
  async getIngestionStatus(taskId: string): Promise<{
    task_id: string;
    status: string;
    progress: number;
    message: string;
  }> {
    const response = await apiClient.get(`/knowledge/ingestion/${taskId}/status`);
    return response.data;
  },
};

export default knowledgeService;
