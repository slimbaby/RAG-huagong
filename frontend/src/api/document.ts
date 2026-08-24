import apiClient from './client';
import type { DocumentInfo } from '../types';

export async function uploadDocument(file: File, onProgress?: (percent: number) => void): Promise<{ doc_id: string; message: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await apiClient.post('/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (progressEvent) => {
      if (onProgress && progressEvent.total) {
        const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
        onProgress(percent);
      }
    },
  });
  return response.data;
}

export async function getDocuments(page: number = 1, pageSize: number = 20, fileType?: string) {
  const response = await apiClient.get('/documents/', { params: { page, page_size: pageSize, file_type: fileType } });
  return response.data;
}

export async function getDocument(docId: string): Promise<DocumentInfo> {
  const response = await apiClient.get(`/documents/${docId}`);
  return response.data;
}

export async function deleteDocument(docId: string) {
  const response = await apiClient.delete(`/documents/${docId}`);
  return response.data;
}

export async function downloadDocument(docId: string, filename: string) {
  const response = await apiClient.get(`/documents/${docId}/download`, { responseType: 'blob' });
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

export async function getDocumentChunks(docId: string) {
  const response = await apiClient.get(`/documents/${docId}/chunks`);
  return response.data;
}

export async function getDocumentProgress(docId: string): Promise<{ status: string; pages_done: number; pages_total: number; stage: string }> {
  const response = await apiClient.get(`/documents/${docId}/progress`);
  return response.data;
}

export async function reuploadDocument(docId: string, file: File, onProgress?: (percent: number) => void): Promise<{ doc_id: string; message: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await apiClient.post(`/documents/${docId}/reupload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (progressEvent) => {
      if (onProgress && progressEvent.total) {
        const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
        onProgress(percent);
      }
    },
  });
  return response.data;
}
