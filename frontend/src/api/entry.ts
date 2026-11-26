import apiClient from './client';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api';

export interface EntryFormData {
  driver_name?: string;
  gender?: string;
  id_no?: string;
  phone?: string;
  plate_no?: string;
  vehicle_type?: string;
  load_capacity?: string;
  company_name?: string;
  chemical_name?: string;
  chemical_type?: string;
  weight_ton?: string | number;
  entry_time?: string;
  exit_time?: string;
  attachments?: Record<string, string>;
  [key: string]: any;
}

export interface EntryApplication {
  id: string;
  applicant_username: string;
  // 方案乙：只有一条写入路径，source 恒为 manual
  source: 'manual';
  // 是否由对话 Agent 预填（用于统计 AI 使用率，不改变提交路径）
  assisted_by_ai: boolean;
  status: 'draft' | 'pending' | 'approved' | 'rejected' | 'cancelled';
  form_data: EntryFormData;
  driver_name: string;
  phone: string;
  plate_no: string;
  company_name: string;
  entry_time: string | null;
  exit_time: string | null;
  review_comment: string;
  reviewed_by: string;
  reviewed_at: string | null;
  confirmed_at: string | null;
  pass_no: string;
  qr_payload: string;
  pass_issued_at: string | null;
  workflow_run_id: string;
  session_id: string;
  created_at: string;
  updated_at: string;
}

export interface EntryPass {
  application_id: string;
  pass_no: string;
  payload: string;
  issued_at: string | null;
  qr_svg: string;
}

export interface AttachmentUploadResult {
  id: string;
  kind: string;
  label: string;
  filename: string;
  url: string;
}

export const ATTACHMENT_KINDS = [
  { key: 'vehicle_front', label: '车头照片' },
  { key: 'vehicle_rear', label: '车尾照片' },
  { key: 'vehicle_license', label: '行驶证' },
  { key: 'driving_license', label: '驾驶证' },
  { key: 'hazmat_license', label: '危化品运输证' },
];

export const FORM_FIELDS = [
  { key: 'driver_name', label: '司机姓名', group: 'driver' },
  { key: 'gender', label: '性别', group: 'driver' },
  { key: 'id_no', label: '身份证号', group: 'driver' },
  { key: 'phone', label: '手机号', group: 'driver' },
  { key: 'plate_no', label: '车牌号', group: 'vehicle' },
  { key: 'vehicle_type', label: '车辆类型', group: 'vehicle' },
  { key: 'load_capacity', label: '车辆承重', group: 'vehicle' },
  { key: 'company_name', label: '申请公司名称', group: 'apply' },
  { key: 'chemical_name', label: '运输化学品名称', group: 'apply' },
  { key: 'chemical_type', label: '化学品类型', group: 'apply' },
  { key: 'weight_ton', label: '运输重量（吨）', group: 'apply' },
  { key: 'entry_time', label: '入园时间', group: 'apply' },
  { key: 'exit_time', label: '出园时间', group: 'apply' },
];

export const STATUS_LABELS: Record<string, string> = {
  draft: '草稿（待确认）',
  pending: '待审核',
  approved: '审核通过',
  rejected: '已驳回',
  cancelled: '已撤回',
};

export const STATUS_COLORS: Record<string, string> = {
  draft: 'default',
  pending: 'processing',
  approved: 'success',
  rejected: 'error',
  cancelled: 'default',
};

export const SOURCE_LABELS: Record<string, string> = {
  manual: 'Web 手动申请',
  assisted: 'AI 辅助填表',
};

/** 方案乙用 assisted_by_ai 标记 AI 辅助，不再有 ai_agent 来源。 */
export const sourceLabel = (app: EntryApplication): string =>
  app.assisted_by_ai ? SOURCE_LABELS.assisted : SOURCE_LABELS.manual;

/** 从聊天历史原文重抽全量字段（状态丢失时恢复；后端只读、不落库）。 */
export const parseEntryText = async (text: string, slots: Record<string, any> = {}) => {
  const res = await apiClient.post('/entry/parse', { text, slots });
  return res.data as {
    slots: Record<string, any>;
    missing: string[];
    errors: string[];
    complete: boolean;
    card?: any;
  };
};

export const getEntryMeta = async () => (await apiClient.get('/entry/meta')).data;

export const uploadEntryAttachment = async (file: File, kind: string): Promise<AttachmentUploadResult> => {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('kind', kind);
  const res = await apiClient.post('/entry/attachments', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
};

/** 附件接口受 JWT 保护，img 标签不能带 Authorization，需先取 blob 再生成临时 URL。 */
export const fetchAttachmentBlobUrl = async (id: string): Promise<string> => {
  const res = await apiClient.get(`/entry/attachments/${id}`, { responseType: 'blob' });
  const type = (res.headers?.['content-type'] as string) || 'image/jpeg';
  return URL.createObjectURL(new Blob([res.data], { type }));
};

export const createEntryApplication = async (
  formData: EntryFormData,
  assistedByAi = false,
): Promise<EntryApplication> =>
  (await apiClient.post('/entry/applications', {
    form_data: formData,
    assisted_by_ai: assistedByAi,
  })).data;

/** 前后端双端校验共用一套规则（时间先后、重量上限、必填项）。 */
export const validateEntryForm = async (formData: EntryFormData, forSubmit = true) =>
  (await apiClient.post('/entry/validate', { form_data: formData, for_submit: forSubmit })).data as {
    missing: string[];
    errors: string[];
    complete: boolean;
  };

export const updateEntryApplication = async (id: string, formData: EntryFormData): Promise<EntryApplication> =>
  (await apiClient.put(`/entry/applications/${id}`, { form_data: formData })).data;

export const getEntryApplication = async (id: string): Promise<EntryApplication> =>
  (await apiClient.get(`/entry/applications/${id}`)).data;

export const submitEntryApplication = async (id: string): Promise<EntryApplication> =>
  (await apiClient.post(`/entry/applications/${id}/submit`)).data;

export const cancelEntryApplication = async (id: string): Promise<EntryApplication> =>
  (await apiClient.post(`/entry/applications/${id}/cancel`)).data;

/** 驳回后改原单重提：rejected → draft，保留单号与完整审计链。 */
export const reopenEntryApplication = async (id: string): Promise<EntryApplication> =>
  (await apiClient.post(`/entry/applications/${id}/reopen`)).data;

/** 审核通过后的通行证编号 + 二维码 SVG（data URI）。 */
export const getEntryPass = async (id: string): Promise<EntryPass> =>
  (await apiClient.get(`/entry/applications/${id}/pass`)).data;

export const listMyEntryApplications = async (status?: string): Promise<{ total: number; applications: EntryApplication[] }> =>
  (await apiClient.get('/entry/applications', { params: { status, limit: 100 } })).data;

export const listReviewEntries = async (status = 'pending'): Promise<{ total: number; applications: EntryApplication[] }> =>
  (await apiClient.get('/entry/review', { params: { status, limit: 100 } })).data;

export const reviewEntryApplication = async (id: string, approved: boolean, comment = ''): Promise<EntryApplication> =>
  (await apiClient.post(`/entry/applications/${id}/review`, { approved, comment })).data;

export const listAllEntryApplications = async (status?: string): Promise<{ total: number; applications: EntryApplication[] }> =>
  (await apiClient.get('/entry/applications', { params: { status, scope: 'all', limit: 100 } })).data;

export const getEntryAudit = async (id: string): Promise<{ logs: any[] }> =>
  (await apiClient.get(`/entry/applications/${id}/audit`)).data;

export const attachmentDownloadUrl = (id: string) => `${API_BASE}/entry/attachments/${id}?download=true`;
