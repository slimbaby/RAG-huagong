import { create } from 'zustand';
import type { ChatMessage, StepProgress } from '../types';
import { STEP_LABELS } from '../types';
import { sendQuestion, sendQuestionStream, getSessions, getSessionHistory } from '../api/chat';

export interface Session {
  id: string;
  title: string;
  createdAt: number;
  lastActive: number;
}

interface ChatState {
  // 当前会话
  messages: ChatMessage[];
  isLoading: boolean;
  sessionId: string;
  error: string | null;
  abortController: AbortController | null;
  // 会话列表
  sessions: Session[];
  sessionsLoaded: boolean;
  // 操作
  addMessage: (message: ChatMessage) => void;
  sendMessage: (question: string) => Promise<void>;
  clearMessages: () => void;
  setError: (error: string | null) => void;
  cancelRequest: () => void;
  // 会话管理
  createSession: () => string;
  switchSession: (sessionId: string) => void;
  deleteSession: (sessionId: string) => void;
  loadSessions: () => Promise<void>;
}

const STORAGE_KEY = 'rag_chat_sessions';
const MESSAGES_PREFIX = 'rag_chat_messages_';

const generateSessionId = () =>
  `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

// localStorage 持久化
const loadSessions = (): Session[] => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
};

const saveSessions = (sessions: Session[]) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  } catch { /* ignore */ }
};

const loadMessages = (sessionId: string): ChatMessage[] => {
  try {
    const raw = localStorage.getItem(MESSAGES_PREFIX + sessionId);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    // 恢复 Date 对象
    return parsed.map((m: any) => ({ ...m, timestamp: new Date(m.timestamp) }));
  } catch {
    return [];
  }
};

const saveMessages = (sessionId: string, messages: ChatMessage[]) => {
  try {
    localStorage.setItem(MESSAGES_PREFIX + sessionId, JSON.stringify(messages));
  } catch { /* ignore */ }
};

const deleteMessages = (sessionId: string) => {
  try {
    localStorage.removeItem(MESSAGES_PREFIX + sessionId);
  } catch { /* ignore */ }
};

const generateTitle = (messages: ChatMessage[]): string => {
  const firstUser = messages.find((m) => m.role === 'user');
  if (firstUser) {
    const text = firstUser.content.slice(0, 20);
    return text.length < firstUser.content.length ? text + '...' : text;
  }
  return '新对话';
};

// 初始化：加载会话列表，或创建默认会话
const initSessions = (): { sessions: Session[]; currentId: string } => {
  const sessions = loadSessions();
  if (sessions.length > 0) {
    return { sessions, currentId: sessions[0].id };
  }
  const id = generateSessionId();
  const session: Session = { id, title: '新对话', createdAt: Date.now(), lastActive: Date.now() };
  return { sessions: [session], currentId: id };
};

// 从后端加载会话列表
const fetchSessionsFromBackend = async (): Promise<Session[]> => {
  try {
    const data = await getSessions(50);
    const backendSessions = (data.sessions || [])
      .filter((s: any) => !s.session_id.startsWith('eval_') && !s.session_id.startsWith('test_'))
      .map((s: any) => ({
        id: s.session_id,
        title: s.preview ? s.preview.slice(0, 20) + (s.preview.length > 20 ? '...' : '') : '新对话',
        createdAt: s.last_active * 1000,
        lastActive: s.last_active * 1000,
      }));
    return backendSessions;
  } catch {
    return [];
  }
};

const init = initSessions();

export const useChatStore = create<ChatState>((set, get) => ({
  messages: loadMessages(init.currentId),
  isLoading: false,
  sessionId: init.currentId,
  error: null,
  abortController: null,
  sessions: init.sessions,
  sessionsLoaded: false,

  addMessage: (message: ChatMessage) => {
    set((state) => {
      const newMessages = [...state.messages, message];
      saveMessages(state.sessionId, newMessages);
      return { messages: newMessages };
    });
  },

  cancelRequest: () => {
    const { abortController } = get();
    if (abortController) {
      abortController.abort();
      set({ abortController: null, isLoading: false });
    }
  },

  sendMessage: async (question: string) => {
    const { sessionId, addMessage } = get();
    const userMessage: ChatMessage = {
      id: `user_${Date.now()}`,
      role: 'user',
      content: question,
      timestamp: new Date(),
    };
    addMessage(userMessage);

    const loadingId = `assistant_${Date.now()}`;
    const loadingMessage: ChatMessage = {
      id: loadingId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isLoading: true,
      steps: [],
    };
    addMessage(loadingMessage);

    const abortController = new AbortController();
    set({ isLoading: true, error: null, abortController });

    const request = { question, session_id: sessionId };

    try {
      await sendQuestionStream(
        request,
        abortController.signal,
        // onStep
        (step: StepProgress) => {
          const label = STEP_LABELS[step.node] || step.node;
          set((state) => ({
            messages: state.messages.map((msg) => {
              if (msg.id !== loadingId) return msg;
              const existing = msg.steps || [];
              const idx = existing.findIndex((s) => s.node === step.node);
              const newStep: StepProgress = { ...step, label };
              const steps = idx >= 0
                ? existing.map((s, i) => i === idx ? newStep : s)
                : [...existing, newStep];
              return { ...msg, steps, currentStep: label };
            }),
          }));
        },
        // onDone
        (response) => {
          set((state) => {
            const newMessages = state.messages.map((msg) =>
              msg.id === loadingId
                ? { ...msg, content: response.answer, citations: response.citations, isLoading: false, currentStep: undefined }
                : msg
            );
            saveMessages(sessionId, newMessages);
            // 更新会话标题和活跃时间
            const sessions = state.sessions.map((s) =>
              s.id === sessionId
                ? { ...s, title: generateTitle(newMessages), lastActive: Date.now() }
                : s
            );
            saveSessions(sessions);
            return { messages: newMessages, isLoading: false, abortController: null, sessions };
          });
        },
        // onError
        (errorMsg: string) => {
          set((state) => ({
            messages: state.messages.map((msg) =>
              msg.id === loadingId
                ? { ...msg, content: `抱歉，出现错误: ${errorMsg}`, isLoading: false, currentStep: undefined }
                : msg
            ),
            isLoading: false,
            error: errorMsg,
            abortController: null,
          }));
        },
      );
    } catch (error: any) {
      if (error.name === 'CanceledError' || error.name === 'AbortError') {
        set((state) => ({
          messages: state.messages.filter((msg) => msg.id !== loadingId),
          isLoading: false,
          abortController: null,
        }));
        return;
      }
      // 降级到普通请求
      try {
        const response = await sendQuestion(request, abortController.signal);
        set((state) => {
          const newMessages = state.messages.map((msg) =>
            msg.id === loadingId
              ? { ...msg, content: response.answer, citations: response.citations, isLoading: false }
              : msg
          );
          saveMessages(sessionId, newMessages);
          return { messages: newMessages, isLoading: false, abortController: null };
        });
      } catch (fallbackError: any) {
        const errorMessage = fallbackError.message || '发送失败';
        set((state) => ({
          messages: state.messages.map((msg) =>
            msg.id === loadingId
              ? { ...msg, content: `抱歉，出现错误: ${errorMessage}`, isLoading: false }
              : msg
          ),
          isLoading: false,
          error: errorMessage,
          abortController: null,
        }));
      }
    }
  },

  clearMessages: () => {
    get().cancelRequest();
    set((state) => {
      const newId = generateSessionId();
      const newSession: Session = { id: newId, title: '新对话', createdAt: Date.now(), lastActive: Date.now() };
      const sessions = [newSession, ...state.sessions];
      saveSessions(sessions);
      return { messages: [], sessionId: newId, error: null, sessions };
    });
  },

  createSession: () => {
    const newId = generateSessionId();
    set((state) => {
      const newSession: Session = { id: newId, title: '新对话', createdAt: Date.now(), lastActive: Date.now() };
      const sessions = [newSession, ...state.sessions];
      saveSessions(sessions);
      return { messages: [], sessionId: newId, error: null, sessions };
    });
    return newId;
  },

  switchSession: async (sessionId: string) => {
    const { cancelRequest } = get();
    cancelRequest();
    let messages = loadMessages(sessionId);
    set({ messages, sessionId, error: null, isLoading: false });
    // 如果本地没有消息，从后端加载
    if (messages.length === 0) {
      try {
        const data = await getSessionHistory(sessionId, 60);
        if (data.history && data.history.length > 0) {
          const backendMessages: ChatMessage[] = data.history.map((m: any, i: number) => ({
            id: `${m.role}_${Date.now()}_${i}`,
            role: m.role as 'user' | 'assistant',
            content: m.content,
            timestamp: new Date(m.timestamp * 1000),
            citations: m.citations,
          }));
          saveMessages(sessionId, backendMessages);
          set({ messages: backendMessages });
        }
      } catch {
        // ignore
      }
    }
  },

  deleteSession: (sessionId: string) => {
    set((state) => {
      const sessions = state.sessions.filter((s) => s.id !== sessionId);
      deleteMessages(sessionId);
      saveSessions(sessions);
      // 如果删除的是当前会话，切换到第一个
      if (state.sessionId === sessionId) {
        if (sessions.length > 0) {
          const messages = loadMessages(sessions[0].id);
          return { messages, sessionId: sessions[0].id, sessions };
        }
        // 没有会话了，创建新的
        const newId = generateSessionId();
        const newSession: Session = { id: newId, title: '新对话', createdAt: Date.now(), lastActive: Date.now() };
        saveSessions([newSession]);
        return { messages: [], sessionId: newId, sessions: [newSession] };
      }
      return { sessions };
    });
  },

  loadSessions: async () => {
    if (get().sessionsLoaded) return;
    const backendSessions = await fetchSessionsFromBackend();
    if (backendSessions.length === 0) {
      set({ sessionsLoaded: true });
      return;
    }
    set((state) => {
      const localIds = new Set(state.sessions.map(s => s.id));
      const merged = [...state.sessions];
      for (const bs of backendSessions) {
        if (!localIds.has(bs.id)) {
          merged.push(bs);
        }
      }
      merged.sort((a, b) => b.lastActive - a.lastActive);
      saveSessions(merged);
      return { sessions: merged, sessionsLoaded: true };
    });
  },

  setError: (error: string | null) => {
    set({ error });
  },
}));
