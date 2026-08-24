import { create } from 'zustand';
import type { EvalReportSummary, EvalReportDetail, EvalCase, EvalDatasetStats } from '../types';
import {
  getEvalReports,
  getEvalReport,
  getEvalDataset,
  getEvalDatasetStats,
  runEvaluation,
  getEvalTaskStatus,
} from '../api/evaluation';

interface EvalState {
  reports: EvalReportSummary[];
  currentReport: EvalReportDetail | null;
  dataset: EvalCase[];
  datasetStats: EvalDatasetStats | null;
  isLoading: boolean;
  error: string | null;

  fetchReports: () => Promise<void>;
  fetchReport: (evalId: string) => Promise<void>;
  fetchDataset: () => Promise<void>;
  fetchDatasetStats: () => Promise<void>;
  runEval: (params?: { layer?: string; gate_enabled?: boolean; include_stale?: boolean }) => Promise<string>;
  pollTaskStatus: (taskId: string) => Promise<any>;
  clearCurrentReport: () => void;
}

export const useEvalStore = create<EvalState>((set) => ({
  reports: [],
  currentReport: null,
  dataset: [],
  datasetStats: null,
  isLoading: false,
  error: null,

  fetchReports: async () => {
    set({ isLoading: true, error: null });
    try {
      const data = await getEvalReports();
      set({ reports: data.reports || [], isLoading: false });
    } catch (error: any) {
      set({ isLoading: false, error: error.message || '获取报告列表失败' });
    }
  },

  fetchReport: async (evalId: string) => {
    set({ isLoading: true, error: null });
    try {
      const data = await getEvalReport(evalId);
      set({ currentReport: data, isLoading: false });
    } catch (error: any) {
      set({ isLoading: false, error: error.message || '获取报告详情失败' });
    }
  },

  fetchDataset: async () => {
    set({ isLoading: true, error: null });
    try {
      const data = await getEvalDataset();
      set({ dataset: data.cases || [], isLoading: false });
    } catch (error: any) {
      set({ isLoading: false, error: error.message || '获取评测集失败' });
    }
  },

  fetchDatasetStats: async () => {
    try {
      const data = await getEvalDatasetStats();
      set({ datasetStats: data });
    } catch {
      // silent
    }
  },

  runEval: async (params) => {
    const data = await runEvaluation(params);
    return data.task_id;
  },

  pollTaskStatus: async (taskId: string) => {
    return await getEvalTaskStatus(taskId);
  },

  clearCurrentReport: () => set({ currentReport: null }),
}));
