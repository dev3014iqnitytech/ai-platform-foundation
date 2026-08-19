/**
 * Approvals Service — API client for human review and approval workflow
 */
import { apiClient } from './api.client';
import type { TestCase } from './testcases.service';

export type ApprovalStatus = 'DRAFT' | 'IN_REVIEW' | 'APPROVED' | 'REJECTED' | 'PUBLISHED';

export interface ApprovalHistoryEntry {
  id: string;
  session_id: string;
  action: string;
  actor_id: string;
  actor_name: string;
  comment?: string;
  previous_status: ApprovalStatus;
  new_status: ApprovalStatus;
  created_at: string;
}

export interface ReviewComment {
  id: string;
  test_case_id?: string;     // null = session-level comment
  author_id: string;
  author_name: string;
  content: string;
  resolved: boolean;
  created_at: string;
}

export interface ApprovalQueueItem {
  session_id: string;
  user_story_id: string;
  story_title: string;
  project_key: string;
  status: ApprovalStatus;
  test_case_count: number;
  revision_count: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  pending_comments: number;
}

export interface ApprovalQueueResponse {
  items: ApprovalQueueItem[];
  total: number;
  page: number;
  size: number;
}

export interface ReviewRequest {
  session_id: string;
  action: 'approve' | 'reject';
  comments?: string;
  test_case_edits?: { id: string; changes: Partial<TestCase> }[];
}

export interface ReviewResponse {
  session_id: string;
  status: ApprovalStatus;
  message: string;
  ado_result?: {
    test_plan_id: number;
    test_case_ids: number[];
  };
}

export interface BulkReviewRequest {
  session_ids: string[];
  action: 'approve' | 'reject';
  comments?: string;
}

const approvalsService = {
  /**
   * Fetch the approval queue (items awaiting review)
   */
  async getQueue(params?: {
    page?: number;
    size?: number;
    status?: ApprovalStatus;
    project_key?: string;
  }): Promise<ApprovalQueueResponse> {
    const response = await apiClient.get<ApprovalQueueResponse>('/approvals/queue', { params });
    return response.data;
  },

  /**
   * Get full review details for a session (test cases + comments)
   */
  async getReviewDetails(sessionId: string): Promise<{
    session: ApprovalQueueItem;
    test_cases: TestCase[];
    comments: ReviewComment[];
  }> {
    const response = await apiClient.get(`/approvals/${sessionId}/review`);
    return response.data;
  },

  /**
   * Approve or reject a review — triggers ADO publish on approval
   */
  async submitReview(request: ReviewRequest): Promise<ReviewResponse> {
    const response = await apiClient.post<ReviewResponse>('/approvals/review', request);
    return response.data;
  },

  /**
   * Bulk approve or reject multiple sessions
   */
  async bulkReview(request: BulkReviewRequest): Promise<{
    succeeded: string[];
    failed: string[];
  }> {
    const response = await apiClient.post('/approvals/bulk-review', request);
    return response.data;
  },

  /**
   * Add a comment to a session or specific test case
   */
  async addComment(
    sessionId: string,
    content: string,
    testCaseId?: string
  ): Promise<ReviewComment> {
    const response = await apiClient.post<ReviewComment>(
      `/approvals/${sessionId}/comments`,
      { content, test_case_id: testCaseId }
    );
    return response.data;
  },

  /**
   * Mark a comment as resolved
   */
  async resolveComment(sessionId: string, commentId: string): Promise<void> {
    await apiClient.patch(`/approvals/${sessionId}/comments/${commentId}/resolve`);
  },

  /**
   * Get approval history (all past decisions with timestamps)
   */
  async getHistory(sessionId: string): Promise<ApprovalHistoryEntry[]> {
    const response = await apiClient.get<ApprovalHistoryEntry[]>(
      `/approvals/${sessionId}/history`
    );
    return response.data;
  },

  /**
   * Request changes (sends back to AI for revision with feedback)
   */
  async requestChanges(sessionId: string, feedback: string): Promise<ReviewResponse> {
    return approvalsService.submitReview({
      session_id: sessionId,
      action: 'reject',
      comments: feedback,
    });
  },
};

export default approvalsService;
