/**
 * Audit Logs Service — API client for immutable audit trail operations
 */
import { apiClient } from './api.client';

export interface AuditLogEntry {
  id: string;
  session_id?: string;
  actor_id?: string;
  actor_name?: string;
  action: string;
  entity_type?: string;
  entity_id?: string;
  payload?: Record<string, unknown>;
  ip_address?: string;
  user_agent?: string;
  created_at: string;
}

export interface AuditLogListResponse {
  items: AuditLogEntry[];
  total: number;
  page: number;
  size: number;
}

export interface AuditLogFilters {
  page?: number;
  size?: number;
  action?: string;
  actor_id?: string;
  session_id?: string;
  entity_type?: string;
  from_date?: string;
  to_date?: string;
}

const auditService = {
  /**
   * List audit log entries with filtering and pagination
   */
  async list(filters?: AuditLogFilters): Promise<AuditLogListResponse> {
    const response = await apiClient.get<AuditLogListResponse>('/audit/logs', {
      params: filters,
    });
    return response.data;
  },

  /**
   * Get a single audit log entry
   */
  async getById(logId: string): Promise<AuditLogEntry> {
    const response = await apiClient.get<AuditLogEntry>(`/audit/logs/${logId}`);
    return response.data;
  },

  /**
   * Get all audit logs for a specific session
   */
  async getBySession(sessionId: string): Promise<AuditLogEntry[]> {
    const response = await apiClient.get<AuditLogListResponse>('/audit/logs', {
      params: { session_id: sessionId, size: 200 },
    });
    return response.data.items;
  },

  /**
   * Export audit logs as CSV
   */
  async exportCsv(filters?: AuditLogFilters): Promise<Blob> {
    const response = await apiClient.get('/audit/export/csv', {
      params: filters,
      responseType: 'blob',
    });
    return response.data as unknown as Blob;
  },

  /**
   * Download CSV export — triggers browser download
   */
  async downloadCsv(filters?: AuditLogFilters): Promise<void> {
    const blob = await auditService.exportCsv(filters);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-logs-${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  },

  /**
   * Get distinct action types for filter dropdown
   */
  async getActionTypes(): Promise<string[]> {
    const response = await apiClient.get<string[]>('/audit/action-types');
    return response.data;
  },
};

export default auditService;
