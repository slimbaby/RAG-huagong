export interface StepProgress {
  node: string;
  label: string;
  status: 'running' | 'completed' | 'error';
  data?: any;
}

export const STEP_LABELS: Record<string, string> = {
  query_optimize: '优化查询',
  vectorize: '向量化',
  retrieval: '知识库检索',
  reranker: '精排',
  check_confidence: '检索置信度检查',
  generate: '生成回答',
  tool_call: '工具调用',
};

export interface FlowCardItem {
  key: string;
  label: string;
  value: string;
  filled: boolean;
}

export interface FlowCard {
  title: string;
  groups: { key: string; label: string; items: FlowCardItem[] }[];
  attachments: (FlowCardItem & { id?: string })[];
}

/** 通用流程结构化产物：由后端 FlowDefinition.build_payload 下发，前端只渲染。 */
export interface FlowPayload {
  __flow_payload__?: boolean;
  __entry_payload__?: boolean;
  flow_id?: string;
  definition_version?: number;
  slots: Record<string, any>;
  missing: string[];
  errors: string[];
  complete: boolean;
  card?: FlowCard | null;
  hint?: string;
  note?: string;
  attachments?: any[];
  rule_check?: { missing: string[]; errors: string[]; complete: boolean };
}

/** 后端权威意图路由下发的候选卡数据（D10：前端零判定规则）。 */
export interface IntentCandidate {
  flow_id: string;
  name: string;
  action: string;
  hint?: string;
}

export interface IntentChoice {
  type?: string;
  candidates: IntentCandidate[];
  hint: string;
  question: string;
  flow_id?: string;
}

// 兼容旧入园命名：类型别名，不是两套模型
export type EntryCardItem = FlowCardItem;
export type EntryCard = FlowCard;
export type EntryPayload = FlowPayload;

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  citations?: CitationInfo[];
  isLoading?: boolean;
  steps?: StepProgress[];
  currentStep?: string;
  flowPayload?: FlowPayload | null;
  /** @deprecated 用 flowPayload */
  entryPayload?: EntryPayload | null;
}

export interface CitationInfo {
  citation_id: number;
  source_file: string;
  page_number?: number;
  line_start?: number;
  content_preview: string;
}

export interface ChatRequest {
  question: string;
  session_id?: string;
  stream?: boolean;
  // ── 通用流程（前端只回传状态，不做意图判定）──
  flow_id?: string;
  flow_slots?: Record<string, any>;
  flow_force?: boolean;
  flow_action?: 'action' | 'readonly' | 'attach' | 'status';
  intent_resolved?: boolean;
  definition_version?: number;
  /** @deprecated 兼容旧入园前端 / E2E */
  entry_slots?: Record<string, any>;
  /** @deprecated 兼容旧入园前端 / E2E */
  entry_force?: boolean;
}

export interface ChatResponse {
  task_id: string;
  trace_id?: string;
  answer: string;
  citations: CitationInfo[];
  optimization?: {
    original: string;
    rewritten: string;
    type: string;
    keywords: string[];
  };
  flow_id?: string | null;
  flow_action?: string | null;
  flow_slots?: Record<string, any>;
  flow_payload?: FlowPayload | null;
  intent_choice?: IntentChoice | null;
  definition_version?: number;
  /** @deprecated */
  entry_action?: boolean;
  /** @deprecated */
  entry_slots?: Record<string, any>;
  /** @deprecated */
  entry_payload?: EntryPayload | null;
  timestamp: string;
}

export interface DocumentInfo {
  doc_id: string;
  filename: string;
  file_type: string;
  file_size: number;
  chunk_count: number;
  upload_time: string;
  status: 'processing' | 'ready' | 'error';
  metadata?: Record<string, any>;
  scope_ids?: string[];
}

export interface ScopeInfo {
  scope_id: string;
  name: string;
}

export interface RoleInfo {
  role_id: string;
  role_name: string;
  scope_ids: string[];
}

export interface UploadProgress {
  filename: string;
  progress: number;
  status: 'uploading' | 'processing' | 'success' | 'error';
  error?: string;
  docId?: string;
  pagesInfo?: string;
}

export interface ErrorRecord {
  error_id: string;
  timestamp: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  category: string;
  error_type: string;
  error_message: string;
  trace_id?: string;
  task_id?: string;
  resolved: boolean;
}

export interface TraceInfo {
  trace_id: string;
  task_id?: string;
  user_query: string;
  optimized_query?: string;
  status: string;
  duration_ms?: number;
  answer?: string;
  sources_count: number;
  tokens_used: {
    prompt: number;
    completion: number;
  };
  start_time?: number;
}

export interface TaskCheckpoint {
  task_id: string;
  task_type: string;
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed';
  created_at: string;
  updated_at: string;
  steps: StepCheckpoint[];
}

export interface StepCheckpoint {
  step_id: string;
  step_name: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  started_at?: string;
  completed_at?: string;
  error?: string;
}

export interface ApiResponse<T> {
  data?: T;
  error?: string;
  message?: string;
}

// ── 评测系统类型 ──

export interface EvalReportSummary {
  eval_id: string;
  timestamp: string;
  avg_score: number;
  gate_passed: boolean;
  total_cases: number;
  passed_cases: number;
}

export interface EvalCaseResult {
  case_id: string;
  question: string;
  category: string;
  difficulty: string;
  answer: string;
  overall_score: number;
  passed: boolean;
  fail_reasons: string[];
  scores: {
    rule: number;
    ragas_faithfulness: number;
    ragas_context_precision: number;
    ragas_context_recall: number;
    hallucination: number;
    citation: number;
    consistency: number;
    judge: number;
  };
}

export interface EvalReportDetail {
  eval_id: string;
  timestamp: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  avg_score: number;
  dimension_scores: Record<string, number>;
  gate_passed: boolean;
  gate_reasons: string[];
  case_results: EvalCaseResult[];
}

export interface EvalCase {
  id: string;
  question: string;
  category: string;
  difficulty: string;
  ground_truth: string;
  expected_contains: string[];
  expected_not_contains: string[];
  expected_source: string;
  layer: string;
  stale: boolean;
  source_doc_id: string;
}

export interface EvalDatasetStats {
  total: number;
  by_layer: Record<string, number>;
  stale: number;
  active: number;
}
