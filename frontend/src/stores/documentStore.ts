import { create } from 'zustand';
import type { DocumentInfo, UploadProgress } from '../types';
import { uploadDocument, getDocuments, getDocument, getDocumentProgress, deleteDocument, reuploadDocument } from '../api/document';

interface DocumentState {
  documents: DocumentInfo[];
  uploadProgress: UploadProgress[];
  isLoading: boolean;
  error: string | null;
  fetchDocuments: () => Promise<void>;
  uploadFile: (file: File) => Promise<void>;
  reuploadFile: (docId: string, file: File) => Promise<void>;
  removeDocument: (docId: string) => Promise<void>;
  clearUploadProgress: () => void;
}

export const useDocumentStore = create<DocumentState>((set, get) => ({
  documents: [],
  uploadProgress: [],
  isLoading: false,
  error: null,

  fetchDocuments: async () => {
    set({ isLoading: true, error: null });
    try {
      const response = await getDocuments();
      set({ documents: response.documents || [], isLoading: false });
    } catch (error: any) {
      set({ isLoading: false, error: error.message || '获取文档列表失败' });
    }
  },

  uploadFile: async (file: File) => {
    set((state) => ({
      uploadProgress: [...state.uploadProgress, { filename: file.name, progress: 0, status: 'uploading' }],
    }));
    try {
      const result = await uploadDocument(file, (progress: number) => {
        set((state) => ({
          uploadProgress: state.uploadProgress.map((p) =>
            p.filename === file.name && p.status === 'uploading'
              ? { ...p, progress: Math.min(progress, 100) }
              : p
          ),
        }));
      });

      // 上传完成，开始轮询后台处理状态
      if (result.doc_id) {
        set((state) => ({
          uploadProgress: state.uploadProgress.map((p) =>
            p.filename === file.name ? { ...p, progress: 100, status: 'processing', docId: result.doc_id } : p
          ),
        }));

        // 轮询文档状态（带页码进度）
        const maxPolls = 360; // 最多轮询 360 次（30 分钟）
        for (let i = 0; i < maxPolls; i++) {
          await new Promise((r) => setTimeout(r, 5000));
          try {
            // 优先查进度端点
            const prog = await getDocumentProgress(result.doc_id);
            if (prog.status === 'ready') {
              set((state) => ({
                uploadProgress: state.uploadProgress.map((p) =>
                  p.filename === file.name ? { ...p, progress: 100, status: 'success', pagesInfo: undefined } : p
                ),
              }));
              await get().fetchDocuments();
              return;
            } else if (prog.status === 'error') {
              set((state) => ({
                uploadProgress: state.uploadProgress.map((p) =>
                  p.filename === file.name ? { ...p, status: 'error', error: '处理失败', pagesInfo: undefined } : p
                ),
                error: '文档处理失败',
              }));
              return;
            }
            // 更新页码进度
            if (prog.pages_total > 0) {
              const info = `${prog.pages_done}/${prog.pages_total} 页`;
              set((state) => ({
                uploadProgress: state.uploadProgress.map((p) =>
                  p.filename === file.name ? { ...p, pagesInfo: info } : p
                ),
              }));
            }
          } catch {
            // 轮询出错，继续重试
          }
        }
        // 超时
        set((state) => ({
          uploadProgress: state.uploadProgress.map((p) =>
            p.filename === file.name ? { ...p, status: 'error', error: '处理超时' } : p
          ),
        }));
      } else {
        set((state) => ({
          uploadProgress: state.uploadProgress.map((p) =>
            p.filename === file.name ? { ...p, progress: 100, status: 'success' } : p
          ),
        }));
        await get().fetchDocuments();
      }
    } catch (error: any) {
      set((state) => ({
        uploadProgress: state.uploadProgress.map((p) =>
          p.filename === file.name ? { ...p, status: 'error', error: error.message || '上传失败' } : p
        ),
        error: error.message || '上传失败',
      }));
    }
  },

  removeDocument: async (docId: string) => {
    try {
      await deleteDocument(docId);
      await get().fetchDocuments();
    } catch (error: any) {
      set({ error: error.message || '删除失败' });
    }
  },

  reuploadFile: async (docId: string, file: File) => {
    set((state) => ({
      uploadProgress: [...state.uploadProgress, { filename: file.name, progress: 0, status: 'uploading' }],
    }));
    try {
      const result = await reuploadDocument(docId, file, (progress: number) => {
        set((state) => ({
          uploadProgress: state.uploadProgress.map((p) =>
            p.filename === file.name && p.status === 'uploading'
              ? { ...p, progress: Math.min(progress, 100) }
              : p
          ),
        }));
      });

      if (result.doc_id) {
        set((state) => ({
          uploadProgress: state.uploadProgress.map((p) =>
            p.filename === file.name ? { ...p, progress: 100, status: 'processing', docId: result.doc_id } : p
          ),
        }));

        const maxPolls = 360;
        for (let i = 0; i < maxPolls; i++) {
          await new Promise((r) => setTimeout(r, 5000));
          try {
            const prog = await getDocumentProgress(result.doc_id);
            if (prog.status === 'ready') {
              set((state) => ({
                uploadProgress: state.uploadProgress.map((p) =>
                  p.filename === file.name ? { ...p, progress: 100, status: 'success', pagesInfo: undefined } : p
                ),
              }));
              await get().fetchDocuments();
              return;
            } else if (prog.status === 'error') {
              set((state) => ({
                uploadProgress: state.uploadProgress.map((p) =>
                  p.filename === file.name ? { ...p, status: 'error', error: '处理失败', pagesInfo: undefined } : p
                ),
                error: '文档处理失败',
              }));
              return;
            }
            if (prog.pages_total > 0) {
              const info = `${prog.pages_done}/${prog.pages_total} 页`;
              set((state) => ({
                uploadProgress: state.uploadProgress.map((p) =>
                  p.filename === file.name ? { ...p, pagesInfo: info } : p
                ),
              }));
            }
          } catch {
            // 轮询出错，继续重试
          }
        }
        set((state) => ({
          uploadProgress: state.uploadProgress.map((p) =>
            p.filename === file.name ? { ...p, status: 'error', error: '处理超时' } : p
          ),
        }));
      }
    } catch (error: any) {
      set((state) => ({
        uploadProgress: state.uploadProgress.map((p) =>
          p.filename === file.name ? { ...p, status: 'error', error: error.message || '重新上传失败' } : p
        ),
        error: error.message || '重新上传失败',
      }));
    }
  },

  clearUploadProgress: () => {
    set({ uploadProgress: [] });
  },
}));
