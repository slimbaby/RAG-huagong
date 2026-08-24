import apiClient from './client';

export async function getTraces(params?: { session_id?: string; status?: string; limit?: number }) {
  const response = await apiClient.get('/tracing/traces', { params });
  return response.data;
}

export async function getTrace(traceId: string) {
  const response = await apiClient.get(`/tracing/traces/${traceId}`);
  return response.data;
}

export async function getFlamegraph(traceId: string) {
  const response = await apiClient.get(`/tracing/traces/${traceId}/flamegraph`);
  return response.data;
}
