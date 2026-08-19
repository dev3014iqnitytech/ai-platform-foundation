/**
 * Test Cases Service — API client for test case CRUD operations
 */
import { apiClient } from './api.client';

export interface TestStep {
  step_number: number;
  action: string;
  expected_result: string;
  test_data?: string;
}

export interface TestCase {
  id: string;
  session_id: string;
  title: string;
  type: string; // 'Functional' | 'Boundary' | 'API' | 'Security' | 'Performance' | 'E2E'
  description?: string;
  gherkin_text?: string;
  steps: TestStep[];
  priority: string; // '1' (Critical) | '2' (High) | '3' (Medium) | '4' (Low)
  tags: string[];
  ado_test_case_id?: string;
  version: number;
  created_at: string;
}

export interface TestCaseListResponse {
  items: TestCase[];
  total: number;
  page: number;
  size: number;
  session_id: string;
}

export interface UpdateTestCaseRequest {
  title?: string;
  description?: string;
  gherkin_text?: string;
  steps?: TestStep[];
  priority?: string;
  tags?: string[];
}

export interface ExportRequest {
  session_id: string;
  format: 'json' | 'csv' | 'excel' | 'gherkin';
}

const testCasesService = {
  /**
   * List all test cases for a session
   */
  async listBySession(
    sessionId: string,
    params?: { page?: number; size?: number; type?: string; priority?: string }
  ): Promise<TestCaseListResponse> {
    const response = await apiClient.get<TestCaseListResponse>('/test-cases', {
      params: { session_id: sessionId, ...params },
    });
    return response.data;
  },

  /**
   * Get a single test case by ID
   */
  async getById(testCaseId: string): Promise<TestCase> {
    const response = await apiClient.get<TestCase>(`/test-cases/${testCaseId}`);
    return response.data;
  },

  /**
   * Update a test case (for human reviewer edits)
   */
  async update(testCaseId: string, request: UpdateTestCaseRequest): Promise<TestCase> {
    const response = await apiClient.patch<TestCase>(`/test-cases/${testCaseId}`, request);
    return response.data;
  },

  /**
   * Delete a test case from a session
   */
  async delete(testCaseId: string): Promise<void> {
    await apiClient.delete(`/test-cases/${testCaseId}`);
  },

  /**
   * Get version history for a test case
   */
  async getVersionHistory(testCaseId: string): Promise<TestCase[]> {
    const response = await apiClient.get<TestCase[]>(`/test-cases/${testCaseId}/versions`);
    return response.data;
  },

  /**
   * Export test cases in various formats
   */
  async export(request: ExportRequest): Promise<Blob> {
    const response = await apiClient.post('/test-cases/export', request, {
      responseType: 'blob',
    });
    return response.data as unknown as Blob;
  },

  /**
   * Download the export — triggers browser download
   */
  async downloadExport(sessionId: string, format: ExportRequest['format']): Promise<void> {
    const blob = await testCasesService.export({ session_id: sessionId, format });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `test-cases-${sessionId}.${format}`;
    a.click();
    URL.revokeObjectURL(url);
  },

  /**
   * Get stats summary for a session's test cases
   */
  async getStats(sessionId: string): Promise<{
    total: number;
    by_type: Record<string, number>;
    by_priority: Record<string, number>;
    with_gherkin: number;
  }> {
    const response = await apiClient.get(`/test-cases/stats`, {
      params: { session_id: sessionId },
    });
    return response.data;
  },
};

export default testCasesService;
