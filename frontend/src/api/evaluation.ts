import client from './client';

export const getEvalReports = () =>
  client.get('/eval/reports').then((r) => r.data);

export const getEvalReport = (evalId: string) =>
  client.get(`/eval/report/${evalId}`).then((r) => r.data);

export const getEvalDataset = () =>
  client.get('/eval/dataset').then((r) => r.data);

export const getEvalDatasetStats = () =>
  client.get('/eval/dataset/stats').then((r) => r.data);

export const runEvaluation = (data?: {
  layer?: string;
  gate_enabled?: boolean;
  include_stale?: boolean;
  sample_ratio?: number;
}) => client.post('/eval/run', data || {}).then((r) => r.data);

export const getEvalTaskStatus = (taskId: string) =>
  client.get(`/eval/status/${taskId}`).then((r) => r.data);

export const getStaleCases = () =>
  client.get('/eval/dataset/stale').then((r) => r.data);

export const markStaleByDoc = (docId: string) =>
  client.post(`/eval/dataset/stale/${docId}`).then((r) => r.data);

export const updateEvalDataset = (cases: any[]) =>
  client.put('/eval/dataset', cases).then((r) => r.data);

export const importEvalDataset = (data: { cases: any[]; name?: string }) =>
  client.post('/eval/dataset/import', data).then((r) => r.data);

export const compareReports = (id1: string, id2: string) =>
  client.post(`/eval/compare?eval_id_1=${id1}&eval_id_2=${id2}`).then((r) => r.data);
