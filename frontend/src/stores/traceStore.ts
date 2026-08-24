import { create } from 'zustand';
import type { TraceInfo } from '../types';
import { getTraces } from '../api/trace';

interface TraceState {
  traces: TraceInfo[];
  isLoading: boolean;
  error: string | null;
  fetchTraces: (params?: { status?: string; limit?: number }) => Promise<void>;
}

export const useTraceStore = create<TraceState>((set) => ({
  traces: [],
  isLoading: false,
  error: null,

  fetchTraces: async (params) => {
    set({ isLoading: true, error: null });
    try {
      const data = await getTraces(params);
      set({ traces: Array.isArray(data) ? data : (data.traces || []), isLoading: false });
    } catch (error: any) {
      set({ isLoading: false, traces: [], error: error.message || '获取调用记录失败' });
    }
  },
}));
