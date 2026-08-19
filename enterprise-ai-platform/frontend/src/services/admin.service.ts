/**
 * Admin Service — API client for user management and system configuration
 */
import { apiClient } from './api.client';

export interface User {
  id: string;
  azure_oid: string;
  email: string;
  display_name: string;
  roles: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface UserListResponse {
  items: User[];
  total: number;
  page: number;
  size: number;
}

export interface SystemMetrics {
  sessions_total: number;
  sessions_today: number;
  sessions_approved: number;
  sessions_pending: number;
  test_cases_generated: number;
  average_generation_time_seconds: number;
  token_usage_total: number;
  ado_updates_total: number;
  kb_documents: number;
  cache_hit_rate: number;
}

export interface AgentConfig {
  name: string;
  model: string;
  temperature: number;
  max_tokens: number;
  enabled: boolean;
}

const adminService = {
  // ── Users ──────────────────────────────────────────────────
  async listUsers(params?: { page?: number; size?: number; role?: string }): Promise<UserListResponse> {
    const response = await apiClient.get<UserListResponse>('/admin/users', { params });
    return response.data;
  },

  async getUser(userId: string): Promise<User> {
    const response = await apiClient.get<User>(`/admin/users/${userId}`);
    return response.data;
  },

  async updateUserRoles(userId: string, roles: string[]): Promise<User> {
    const response = await apiClient.patch<User>(`/admin/users/${userId}/roles`, { roles });
    return response.data;
  },

  async deactivateUser(userId: string): Promise<void> {
    await apiClient.delete(`/admin/users/${userId}`);
  },

  // ── Metrics ────────────────────────────────────────────────
  async getMetrics(): Promise<SystemMetrics> {
    const response = await apiClient.get<SystemMetrics>('/admin/metrics');
    return response.data;
  },

  // ── Agent Configuration ────────────────────────────────────
  async listAgentConfigs(): Promise<AgentConfig[]> {
    const response = await apiClient.get<AgentConfig[]>('/admin/agents');
    return response.data;
  },

  async updateAgentConfig(name: string, config: Partial<AgentConfig>): Promise<AgentConfig> {
    const response = await apiClient.patch<AgentConfig>(`/admin/agents/${name}`, config);
    return response.data;
  },

  // ── System Health ──────────────────────────────────────────
  async getHealth(): Promise<{
    status: string;
    database: string;
    redis: string;
    azure_openai: string;
    azure_search: string;
  }> {
    const response = await apiClient.get('/admin/health');
    return response.data;
  },

  async clearCache(): Promise<{ cleared_keys: number }> {
    const response = await apiClient.post('/admin/cache/clear');
    return response.data;
  },
};

export default adminService;
