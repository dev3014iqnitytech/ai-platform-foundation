/**
 * Stories Service — API client for User Story operations
 * Wraps all /api/v1/stories endpoints with typed request/response models
 */
import { apiClient } from './api.client';

export interface UserStory {
  id: string;
  title: string;
  description: string;
  acceptance_criteria: string;
  area_path: string;
  tags: string[];
  state: string;
  work_item_type: string;
}

export interface GenerateRequest {
  user_story_id: string;
  project_key?: string;
  max_test_cases?: number;
  include_types?: string[];
  knowledge_filters?: Record<string, unknown>;
}

export interface GenerateResponse {
  session_id: string;
  status: string;
  message: string;
  user_story_id: string;
}

export interface SessionStatus {
  session_id: string;
  status: string;
  user_story_id: string;
  project_key: string;
  test_case_count: number;
  revision_count: number;
  created_at: string;
  updated_at: string;
}

export interface StoriesListResponse {
  items: SessionStatus[];
  total: number;
  page: number;
  size: number;
}

const storiesService = {
  /**
   * Fetch a User Story from Azure DevOps by ID
   */
  async fetchStory(storyId: string): Promise<UserStory> {
    const response = await apiClient.get<UserStory>(`/stories/${storyId}`);
    return response.data;
  },

  /**
   * Start the AI test generation workflow for a User Story
   */
  async generateTestCases(request: GenerateRequest): Promise<GenerateResponse> {
    const response = await apiClient.post<GenerateResponse>('/stories/generate', request);
    return response.data;
  },

  /**
   * List all test generation sessions with pagination
   */
  async listSessions(params?: {
    page?: number;
    size?: number;
    status?: string;
    project_key?: string;
  }): Promise<StoriesListResponse> {
    const response = await apiClient.get<StoriesListResponse>('/stories/sessions', { params });
    return response.data;
  },

  /**
   * Get the current status of a session
   */
  async getSessionStatus(sessionId: string): Promise<SessionStatus> {
    const response = await apiClient.get<SessionStatus>(`/stories/sessions/${sessionId}`);
    return response.data;
  },

  /**
   * Cancel/delete a generation session
   */
  async cancelSession(sessionId: string): Promise<void> {
    await apiClient.delete(`/stories/sessions/${sessionId}`);
  },

  /**
   * Retry a failed session
   */
  async retrySession(sessionId: string): Promise<GenerateResponse> {
    const response = await apiClient.post<GenerateResponse>(
      `/stories/sessions/${sessionId}/retry`
    );
    return response.data;
  },

  /**
   * Stream session progress via Server-Sent Events
   * Returns an EventSource that emits status updates
   */
  async streamProgress(
    sessionId: string,
    onMessage: (data: unknown) => void,
    onError?: (error: Event) => void
  ): Promise<EventSource> {
    // Acquire token from MSAL (never use localStorage for access tokens)
    const { msalInstance, apiScopes } = await import('@/features/auth/msal.config');
    const accounts = msalInstance.getAllAccounts();
    let token = '';
    if (accounts.length > 0) {
      try {
        const result = await msalInstance.acquireTokenSilent({
          scopes: apiScopes.read,
          account: accounts[0],
        });
        token = result.accessToken;
      } catch {
        // silent refresh failed — EventSource will open without a token and get a 401
      }
    }
    const url = `${import.meta.env.VITE_API_BASE_URL}/api/v1/stories/sessions/${sessionId}/stream`;
    const es = new EventSource(`${url}?token=${encodeURIComponent(token)}`);
    es.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data));
      } catch {
        // ignore parse errors
      }
    };
    if (onError) {
      es.onerror = onError;
    }
    return es;
  },
};

export default storiesService;
