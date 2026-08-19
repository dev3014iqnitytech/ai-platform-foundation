/**
 * Axios API Client — Injects Bearer token on every request.
 * In LOCAL_MODE (VITE_LOCAL_MODE=true) reads token from localStorage.
 * In production uses Azure AD MSAL.
 */
import axios, { AxiosInstance } from 'axios';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '';
const LOCAL_MODE = import.meta.env.VITE_LOCAL_MODE === 'true';

export const LOCAL_TOKEN_KEY = 'eatap_local_token';

function createApiClient(): AxiosInstance {
  const client = axios.create({
    baseURL: `${BASE_URL}/api/v1`,
    headers: { 'Content-Type': 'application/json' },
    timeout: 30_000,
  });

  // Request interceptor: inject Bearer token
  client.interceptors.request.use(async (config) => {
    try {
      if (LOCAL_MODE) {
        const token = localStorage.getItem(LOCAL_TOKEN_KEY);
        if (token) {
          config.headers.Authorization = `Bearer ${token}`;
        }
      } else {
        const { msalInstance, apiScopes } = await import('@/features/auth/msal.config');
        const accounts = msalInstance.getAllAccounts();
        if (accounts.length === 0) return config;

        const tokenResponse = await msalInstance.acquireTokenSilent({
          scopes: apiScopes.read,
          account: accounts[0],
        });
        config.headers.Authorization = `Bearer ${tokenResponse.accessToken}`;
      }
      config.headers['X-Request-Id'] = crypto.randomUUID();
    } catch {
      if (!LOCAL_MODE) {
        const { msalInstance, apiScopes } = await import('@/features/auth/msal.config');
        await msalInstance.acquireTokenRedirect({ scopes: apiScopes.read });
      }
    }
    return config;
  });

  // Response interceptor: handle 401/403
  client.interceptors.response.use(
    (response) => response,
    async (error) => {
      if (error.response?.status === 401 && !LOCAL_MODE) {
        const { msalInstance, apiScopes } = await import('@/features/auth/msal.config');
        await msalInstance.acquireTokenRedirect({ scopes: apiScopes.read });
      }
      return Promise.reject(error);
    }
  );

  return client;
}

export const apiClient = createApiClient();

// ─── Typed service functions ───────────────────────────────────────────────

export const storiesApi = {
  fetchStory: (storyId: string) =>
    apiClient.post('/stories/fetch', { user_story_id: storyId }),

  generateTestCases: (params: {
    user_story_id: string;
    max_test_cases?: number;
    include_types?: string[];
  }) => apiClient.post('/stories/generate', params),

  getSession: (sessionId: string) =>
    apiClient.get(`/stories/sessions/${sessionId}`),

  listSessions: (page = 1, pageSize = 20) =>
    apiClient.get('/stories/sessions', { params: { page, page_size: pageSize } }),
};

export const testCasesApi = {
  getBySession: (sessionId: string, typeFilter?: string) =>
    apiClient.get(`/test-cases/session/${sessionId}`, {
      params: typeFilter ? { type_filter: typeFilter } : undefined,
    }),

  getById: (id: string) => apiClient.get(`/test-cases/${id}`),

  exportSession: (sessionId: string) =>
    apiClient.get(`/test-cases/session/${sessionId}/export`),
};

export const approvalsApi = {
  getQueue: (page = 1, pageSize = 20) =>
    apiClient.get('/approvals/queue', { params: { page, page_size: pageSize } }),

  review: (sessionId: string, action: 'approve' | 'reject', comment?: string) =>
    apiClient.post('/approvals/review', {
      session_id: sessionId,
      action,
      comment,
    }),

  addComment: (sessionId: string, comment: string, testCaseId?: string) =>
    apiClient.post(`/approvals/${sessionId}/comments`, null, {
      params: { comment_text: comment, test_case_id: testCaseId },
    }),

  getComments: (sessionId: string) =>
    apiClient.get(`/approvals/${sessionId}/comments`),

  getHistory: (sessionId: string) =>
    apiClient.get(`/approvals/${sessionId}/history`),
};

export const knowledgeApi = {
  upload: (file: File, category: string) => {
    const form = new FormData();
    form.append('file', file);
    return apiClient.post(`/knowledge/upload?category=${category}`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  search: (query: string, category?: string, topK = 10) =>
    apiClient.post('/knowledge/search', { query, category, top_k: topK }),

  listDocuments: (category?: string, page = 1) =>
    apiClient.get('/knowledge/documents', { params: { category, page } }),

  deleteDocument: (id: string) =>
    apiClient.delete(`/knowledge/documents/${id}`),
};

export const auditApi = {
  list: (params?: { action?: string; page?: number; page_size?: number }) =>
    apiClient.get('/audit/', { params }),

  getBySession: (sessionId: string) =>
    apiClient.get(`/audit/session/${sessionId}`),
};

export const adminApi = {
  listUsers: () => apiClient.get('/admin/users'),
  updateRoles: (userId: string, roles: string[]) =>
    apiClient.patch(`/admin/users/${userId}/roles`, roles),
  getSettings: () => apiClient.get('/admin/settings'),
  getStats: () => apiClient.get('/admin/stats'),
};

export const authApi = {
  getMe: () => apiClient.get('/auth/me'),
  getRoles: () => apiClient.get('/auth/roles'),
  logout: () => apiClient.post('/auth/logout'),
};
