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

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  citations?: CitationInfo[];
  isLoading?: boolean;
  steps?: StepProgress[];
  currentStep?: string;
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
