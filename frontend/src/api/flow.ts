/**
 * 多流程配置化平台 API（docs/10 §8）
 * 前端零判定规则：菜单、字段、卡片都来自后端下发的渲染元数据。
 * 所有运行时业务接口带 flow_id，新增流程不需要改前端代码。
 */
import apiClient from './client';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api';

export interface FlowField {
  key: string;
  label: string;
  group?: string;
  type?: string;
  required?: boolean;
  options?: string[];
  placeholder?: string;
  pii?: boolean;
  visible_when?: { field: string; eq?: any; in?: any[] };
  order?: number;
}

export interface FlowRenderConfig {
  flow_id: string;
  name: string;
  version: number;
  fields: FlowField[];
  groups: { key: string; label: string }[];
  attachments: {
    key: string; label: string; required?: boolean; max_count?: number;
    accept?: string; max_size_mb?: number; reuse_policy?: string;
  }[];
  card: { title: string; field_keys: string[]; prefill_target: string };
  state_labels: Record<string, string>;
  session_prefix: string;
  scope: string;
}

export interface FlowEntryPoint {
  flow_id: string;
  flow_name: string;
  type: 'menu' | 'shortcut';
  key: string;
  label: string;
  path?: string;
  visible_roles: string[];
  icon?: string;
  order?: number;
  scope?: string;
  version?: number;
}

export interface FlowApplication {
  id: string;
  flow_id: string;
  definition_version: number;
  status: 'draft' | 'pending' | 'approved' | 'rejected' | 'cancelled';
  applicant_username: string;
  source: string;
  assisted_by_ai: boolean;
  form_data: Record<string, any>;
  attachments: Record<string, any>;
  result: Record<string, any>;
  title: string;
  review_comment: string;
  reviewed_by: string;
  reviewed_at: string | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
  // 兼容视图列名（老数据）
  pass_no?: string;
  qr_payload?: string;
}

export const STATUS_COLORS: Record<string, string> = {
  draft: 'default', pending: 'processing', approved: 'success',
  rejected: 'error', cancelled: 'default',
};

export const SOURCE_LABELS: Record<string, string> = {
  manual: 'Web 手动申请', assisted: 'AI 辅助填表',
};

// ── 通用渲染元数据 ──

export const getEntryPoints = async (): Promise<{ entry_points: FlowEntryPoint[]; role_id: string }> =>
  (await apiClient.get('/flows/entry-points')).data;

export const getRenderConfig = async (flowId: string): Promise<FlowRenderConfig> =>
  (await apiClient.get('/flows/render-config', { params: { flow_id: flowId } })).data;

export const getFlowMeta = async (flowId: string) =>
  (await apiClient.get(`/flows/${flowId}/meta`)).data;

// ── 通用运行时业务 API ──

export const parseFlowText = async (flowId: string, text: string, slots: Record<string, any> = {}) =>
  (await apiClient.post(`/flows/${flowId}/parse`, { text, slots })).data;

export const validateFlowForm = async (flowId: string, form_data: Record<string, any>, forSubmit = true) =>
  (await apiClient.post(`/flows/${flowId}/form/validate`, { form_data, for_submit: forSubmit })).data as {
    missing: string[]; errors: string[]; complete: boolean;
  };

export const uploadFlowAttachment = async (flowId: string, file: File, kind: string) => {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('kind', kind);
  return (await apiClient.post(`/flows/${flowId}/attachments`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })).data as { id: string; kind: string; label: string; filename: string; url: string };
};

export const fetchFlowAttachmentBlobUrl = async (flowId: string, id: string): Promise<string> => {
  const res = await apiClient.get(`/flows/${flowId}/attachments/${id}`, { responseType: 'blob' });
  const type = (res.headers?.['content-type'] as string) || 'image/jpeg';
  return URL.createObjectURL(new Blob([res.data], { type }));
};

export const attachmentDownloadUrl = (flowId: string, id: string) =>
  `${API_BASE}/flows/${flowId}/attachments/${id}?download=true`;

export const createFlowApplication = async (flowId: string, form_data: Record<string, any>, assistedByAi = false) =>
  (await apiClient.post(`/flows/${flowId}/applications`, {
    form_data, assisted_by_ai: assistedByAi,
  })).data as FlowApplication;

export const updateFlowApplication = async (flowId: string, id: string, form_data: Record<string, any>) =>
  (await apiClient.put(`/flows/${flowId}/applications/${id}`, { form_data })).data as FlowApplication;

export const getFlowApplication = async (flowId: string, id: string) =>
  (await apiClient.get(`/flows/${flowId}/applications/${id}`)).data as FlowApplication;

export const submitFlowApplication = async (flowId: string, id: string) =>
  (await apiClient.post(`/flows/${flowId}/applications/${id}/submit`)).data as FlowApplication;

export const cancelFlowApplication = async (flowId: string, id: string) =>
  (await apiClient.post(`/flows/${flowId}/applications/${id}/cancel`)).data as FlowApplication;

export const reopenFlowApplication = async (flowId: string, id: string) =>
  (await apiClient.post(`/flows/${flowId}/applications/${id}/reopen`)).data as FlowApplication;

export const getFlowPass = async (flowId: string, id: string) =>
  (await apiClient.get(`/flows/${flowId}/applications/${id}/pass`)).data;

export const listFlowApplications = async (flowId: string, status?: string, scope = 'mine') =>
  (await apiClient.get(`/flows/${flowId}/applications`, { params: { status, scope, limit: 100 } })).data as {
    total: number; applications: FlowApplication[]; status_labels: Record<string, string>;
  };

export const listFlowReview = async (flowId: string, status = 'pending') =>
  (await apiClient.get(`/flows/${flowId}/review`, { params: { status, limit: 100 } })).data as {
    total: number; applications: FlowApplication[]; status_labels: Record<string, string>;
  };

export const reviewFlowApplication = async (flowId: string, id: string, approved: boolean, comment = '') =>
  (await apiClient.post(`/flows/${flowId}/applications/${id}/review`, { approved, comment })).data as FlowApplication;

export const getFlowAudit = async (flowId: string, id: string) =>
  (await apiClient.get(`/flows/${flowId}/applications/${id}/audit`)).data as { logs: any[] };

// ── 配置管理后台 API ──

export const listFlows = async () => (await apiClient.get('/flows')).data as { flows: any[] };
export const getFlowTemplate = async (flowId: string, name = '', keyword = '') =>
  (await apiClient.get('/flows/template', { params: { flow_id: flowId, name, keyword } })).data as {
    definition: Record<string, any>;
  };
export const getFlow = async (flowId: string) => (await apiClient.get(`/flows/${flowId}`)).data;
export const getDraft = async (flowId: string) => (await apiClient.get(`/flows/${flowId}/draft`)).data;
export const saveDraft = async (flowId: string, definition: any, expect_draft_revision: number) =>
  (await apiClient.put(`/flows/${flowId}/draft`, { definition, expect_draft_revision })).data;
export const validateConfig = async (flowId: string) =>
  (await apiClient.post(`/flows/${flowId}/validate`)).data;
export const previewConfig = async (flowId: string) =>
  (await apiClient.post(`/flows/${flowId}/preview`)).data;
export const publishFlow = async (flowId: string, expect_version: number, note = '') =>
  (await apiClient.post(`/flows/${flowId}/publish`, { expect_version, note })).data;
export const rollbackFlow = async (flowId: string, version: number, note = '') =>
  (await apiClient.post(`/flows/${flowId}/rollback`, { version, note })).data;
export const listVersions = async (flowId: string) =>
  (await apiClient.get(`/flows/${flowId}/versions`)).data;
export const getVersionDiff = async (flowId: string, version: number) =>
  (await apiClient.get(`/flows/${flowId}/versions/${version}/diff`)).data;
export const listHooks = async () => (await apiClient.get('/flows/hooks')).data;
export const getTestCases = async (flowId: string) =>
  (await apiClient.get(`/flows/${flowId}/test-cases`)).data as { cases: any[] };
export const saveTestCases = async (flowId: string, cases: any[], expect_draft_revision: number) =>
  (await apiClient.put(`/flows/${flowId}/test-cases`, { cases, expect_draft_revision })).data;
export const runTestCases = async (flowId: string) =>
  (await apiClient.post(`/flows/${flowId}/test-cases/run`)).data;
export const getRollout = async (flowId: string) =>
  (await apiClient.get(`/flows/${flowId}/rollout`)).data;
export const setRollout = async (flowId: string, rollout: any) =>
  (await apiClient.post(`/flows/${flowId}/rollout`, rollout)).data;
export const getConfigAudit = async (flowId?: string, limit = 100) =>
  (await apiClient.get('/flows/audit', { params: { flow_id: flowId, limit } })).data;
export const bootstrapFlows = async () => (await apiClient.post('/flows/bootstrap')).data;
