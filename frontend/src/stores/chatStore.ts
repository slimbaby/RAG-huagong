import { create } from 'zustand';
import type {
  ChatMessage, StepProgress, FlowPayload, EntryPayload,
  IntentChoice, IntentCandidate, ChatRequest,
} from '../types';
import { STEP_LABELS } from '../types';
import { sendQuestion, sendQuestionStream, getSessions, getSessionHistory } from '../api/chat';
import { parseFlowText } from '../api/flow';

export interface Session {
  id: string;
  title: string;
  createdAt: number;
  lastActive: number;
}

export interface SendOptions {
  /** 用户在选择卡上点选的流程；后端仍会做权限与规则权威校验 */
  flowId?: string;
  /** 点「办理」：放宽歧义，但不绕过后端权限 */
  force?: boolean;
  action?: 'action' | 'readonly' | 'attach' | 'status';
  /** 选择卡结果一次性标记，防死循环 */
  intentResolved?: boolean;
}

interface FlowMeta {
  flowId: string;
  version: number;
}

interface ChatState {
  messages: ChatMessage[];
  isLoading: boolean;
  sessionId: string;
  error: string | null;
  abortController: AbortController | null;
  sessions: Session[];
  sessionsLoaded: boolean;
  // 通用流程状态：前端只持有，每轮回传，后端不落库
  flowSlots: Record<string, any>;
  flowPayload: FlowPayload | null;
  activeFlowId: string | null;
  definitionVersion: number;
  /** 后端权威候选卡数据；null 表示无需用户点选 */
  pendingIntentChoice: IntentChoice | null;
  // 旧命名别名（兼容历史本地存储与 E2E）
  entrySlots: Record<string, any>;
  entryPayload: EntryPayload | null;

  addMessage: (message: ChatMessage) => void;
  sendMessage: (question: string, opts?: SendOptions) => Promise<void>;
  clearMessages: () => void;
  setError: (error: string | null) => void;
  cancelRequest: () => void;
  setFlowAttachment: (flowId: string, kind: string, id: string) => void;
  applyFlowPayload: (payload: FlowPayload) => void;
  startFlowSession: (flowId?: string, label?: string) => string;
  clearFlowState: () => void;
  setIntentChoice: (choice: IntentChoice | null) => void;
  resolveIntentChoice: (candidate: IntentCandidate | null) => Promise<void>;
  // 兼容别名
  setEntryAttachment: (kind: string, id: string) => void;
  applyEntryPayload: (payload: EntryPayload) => void;
  startEntrySession: () => string;
  // 会话管理
  createSession: () => string;
  switchSession: (sessionId: string) => void;
  deleteSession: (sessionId: string) => void;
  loadSessions: () => Promise<void>;
}

const STORAGE_KEY = 'rag_chat_sessions';
const MESSAGES_PREFIX = 'rag_chat_messages_';
const FLOW_SLOTS_PREFIX = 'rag_flow_slots_';
const LEGACY_SLOTS_PREFIX = 'rag_entry_slots_';
const FLOW_META_PREFIX = 'rag_flow_meta_';
const FLOW_SESSION_PREFIX = 'flow-';

const generateSessionId = (flow = false) =>
  `${flow ? FLOW_SESSION_PREFIX : 'session_'}${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

const loadSessions = (): Session[] => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
};

const saveSessions = (sessions: Session[]) => {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions)); } catch { /* ignore */ }
};

const loadMessages = (sessionId: string): ChatMessage[] => {
  try {
    const raw = localStorage.getItem(MESSAGES_PREFIX + sessionId);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return parsed.map((m: any) => ({
      ...m,
      timestamp: new Date(m.timestamp),
      // 历史消息统一读 flowPayload
      flowPayload: m.flowPayload || m.entryPayload || null,
    }));
  } catch {
    return [];
  }
};

const saveMessages = (sessionId: string, messages: ChatMessage[]) => {
  try { localStorage.setItem(MESSAGES_PREFIX + sessionId, JSON.stringify(messages)); } catch { /* ignore */ }
};

const deleteMessages = (sessionId: string) => {
  try {
    localStorage.removeItem(MESSAGES_PREFIX + sessionId);
    localStorage.removeItem(FLOW_SLOTS_PREFIX + sessionId);
    localStorage.removeItem(LEGACY_SLOTS_PREFIX + sessionId);
    localStorage.removeItem(FLOW_META_PREFIX + sessionId);
  } catch { /* ignore */ }
};

const loadFlowSlots = (sessionId: string): Record<string, any> => {
  try {
    const raw = localStorage.getItem(FLOW_SLOTS_PREFIX + sessionId)
      || localStorage.getItem(LEGACY_SLOTS_PREFIX + sessionId);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
};

const saveFlowSlots = (sessionId: string, slots: Record<string, any>) => {
  try { localStorage.setItem(FLOW_SLOTS_PREFIX + sessionId, JSON.stringify(slots)); } catch { /* ignore */ }
};

const loadFlowMeta = (sessionId: string): FlowMeta => {
  try {
    const raw = localStorage.getItem(FLOW_META_PREFIX + sessionId);
    if (raw) return JSON.parse(raw) as FlowMeta;
  } catch { /* ignore */ }
  // 老 entry- 会话迁移到第一份通用流程（仅迁移标记，不含任何判定规则）
  return { flowId: sessionId.startsWith('entry-') ? 'driver_entry' : '', version: 0 };
};

const saveFlowMeta = (sessionId: string, meta: FlowMeta) => {
  try { localStorage.setItem(FLOW_META_PREFIX + sessionId, JSON.stringify(meta)); } catch { /* ignore */ }
};

const generateTitle = (messages: ChatMessage[]): string => {
  const firstUser = messages.find((m) => m.role === 'user');
  if (firstUser) {
    const text = firstUser.content.slice(0, 20);
    return text.length < firstUser.content.length ? text + '...' : text;
  }
  return '新对话';
};

const initSessions = (): { sessions: Session[]; currentId: string } => {
  const sessions = loadSessions();
  if (sessions.length > 0) return { sessions, currentId: sessions[0].id };
  const id = generateSessionId();
  const session: Session = { id, title: '新对话', createdAt: Date.now(), lastActive: Date.now() };
  return { sessions: [session], currentId: id };
};

const fetchSessionsFromBackend = async (): Promise<Session[]> => {
  try {
    const data = await getSessions(50);
    return (data.sessions || [])
      .filter((s: any) => !s.session_id.startsWith('eval_') && !s.session_id.startsWith('test_'))
      .map((s: any) => ({
        id: s.session_id,
        title: s.preview ? s.preview.slice(0, 20) + (s.preview.length > 20 ? '...' : '') : '新对话',
        createdAt: s.last_active * 1000,
        lastActive: s.last_active * 1000,
      }));
  } catch {
    return [];
  }
};

/** 状态丢失时从聊天历史原文重抽全量字段（后端只读解析，不落库）。 */
const reextractFlowSlots = async (sessionId: string, flowId: string): Promise<Record<string, any>> => {
  if (!flowId) return {};
  try {
    const data = await getSessionHistory(sessionId, 60);
    const text = (data.history || [])
      .filter((m: any) => m.role === 'user')
      .map((m: any) => m.content)
      .join('\n');
    if (!text.trim()) return {};
    const payload = await parseFlowText(flowId, text, {});
    return payload.slots || {};
  } catch {
    return {};
  }
};

const init = initSessions();
const initMeta = loadFlowMeta(init.currentId);
const initSlots = loadFlowSlots(init.currentId);

/** 状态里 flowSlots 与旧别名 entrySlots 永远指向同一份对象，避免双写漂移。 */
const slotPair = (slots: Record<string, any>) => ({ flowSlots: slots, entrySlots: slots });
const payloadPair = (payload: FlowPayload | null) => ({ flowPayload: payload, entryPayload: payload });

export const useChatStore = create<ChatState>((set, get) => ({
  messages: loadMessages(init.currentId),
  isLoading: false,
  sessionId: init.currentId,
  error: null,
  abortController: null,
  sessions: init.sessions,
  sessionsLoaded: false,
  ...slotPair(initSlots),
  ...payloadPair(null),
  activeFlowId: initMeta.flowId || null,
  definitionVersion: initMeta.version || 0,
  pendingIntentChoice: null,

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

  clearFlowState: () => {
    const { sessionId, messages } = get();
    saveFlowSlots(sessionId, {});
    saveFlowMeta(sessionId, { flowId: '', version: 0 });
    // 流程办结（本人已提交）或主动退出时，旧的「信息核对卡」不应继续挂在
    // 历史回答下面：卡片上的「去表单提交」入口在提交后已失效，会误导用户。
    const cleaned = messages.map((m) => ({ ...m, ...payloadPair(null) }));
    saveMessages(sessionId, cleaned);
    set({
      ...slotPair({}), ...payloadPair(null),
      activeFlowId: null, definitionVersion: 0, pendingIntentChoice: null,
      messages: cleaned,
    });
  },

  setFlowAttachment: (flowId: string, kind: string, id: string) => {
    // 上传材料只是「补充当前流程的信息」，绝不能新建会话：
    // flowSlots 按 sessionId 存在 localStorage，一旦切会话，
    // 前面几轮收集到的字段、锁定的配置版本和确认卡都会读不到（曾导致用户信息全丢）。
    // 会话隔离只在用户本人显式点「AI 快捷入口 / 候选卡办理」时发生，且由
    // startFlowSession 把同一流程的槽位带到新会话。
    set((state) => {
      const sid = state.sessionId;
      const attachments = { ...(state.flowSlots.attachments || {}), [kind]: id };
      const slots = { ...state.flowSlots, attachments };
      saveFlowSlots(sid, slots);
      saveFlowMeta(sid, { flowId, version: state.definitionVersion || 0 });
      return { ...slotPair(slots), activeFlowId: flowId };
    });
  },

  applyFlowPayload: (payload: FlowPayload) => {
    set((state) => {
      const slots = { ...(payload.slots || state.flowSlots) };
      saveFlowSlots(state.sessionId, slots);
      const fid = payload.flow_id || state.activeFlowId;
      const version = payload.definition_version || state.definitionVersion;
      if (fid) saveFlowMeta(state.sessionId, { flowId: fid, version });
      return {
        ...slotPair(slots), ...payloadPair(payload),
        ...(fid ? { activeFlowId: fid } : {}),
        ...(version ? { definitionVersion: version } : {}),
      };
    });
  },

  startFlowSession: (flowId = '', label = '') => {
    const newId = generateSessionId(true);
    set((state) => {
      const hasSlots = Object.keys(state.flowSlots || {}).length > 0;
      // 新建独立会话的目的是隔离聊天历史与长期记忆，不是清空业务数据。
      // 同一流程（或当前尚未标记流程但已收集到字段）必须把槽位与锁定的
      // 配置版本带过去；只有切换到别的流程、或本来就是空白会话才从零开始。
      const sameFlow = Boolean(flowId) && (
        state.activeFlowId === flowId || (!state.activeFlowId && hasSlots)
      );
      const carriedSlots = sameFlow ? { ...state.flowSlots } : {};
      const carriedVersion = sameFlow ? state.definitionVersion : 0;
      const carriedPayload = sameFlow ? state.flowPayload : null;
      const newSession: Session = {
        id: newId, title: label || 'AI 帮我办理', createdAt: Date.now(), lastActive: Date.now(),
      };
      const sessions = [newSession, ...state.sessions];
      saveSessions(sessions);
      saveFlowSlots(newId, carriedSlots);
      saveFlowMeta(newId, { flowId, version: carriedVersion });
      return {
        messages: [], sessionId: newId, error: null, sessions,
        ...slotPair(carriedSlots), ...payloadPair(carriedPayload),
        activeFlowId: flowId || null, definitionVersion: carriedVersion,
        pendingIntentChoice: null,
      };
    });
    return newId;
  },

  setIntentChoice: (choice) => set({ pendingIntentChoice: choice }),

  resolveIntentChoice: async (candidate: IntentCandidate | null) => {
    const choice = get().pendingIntentChoice;
    if (!choice) return;
    set({ pendingIntentChoice: null });
    if (candidate) {
      // 本人点「办理」：后端权威校验 flow_id + 角色，前端只带一次性标记
      await get().sendMessage(choice.question, {
        flowId: candidate.flow_id, force: true, action: 'action', intentResolved: true,
      });
    } else {
      // 本人点「咨询」：按原样发，只读动作不再挂办理工具
      await get().sendMessage(choice.question, {
        action: 'readonly', intentResolved: true,
      });
    }
  },

  sendMessage: async (question: string, opts?: SendOptions) => {
    set({ pendingIntentChoice: null });
    let { sessionId, addMessage } = get();
    const targetFlowId = opts?.flowId || get().activeFlowId || '';

    // 显式从快捷入口 / 选择卡进入流程时，开独立会话隔离历史与长期记忆。
    // 前端不判断用户说了什么，只处理「用户本人点了哪个入口」。
    if (opts?.flowId && (get().activeFlowId !== opts.flowId
        || !sessionId.startsWith(FLOW_SESSION_PREFIX))) {
      sessionId = get().startFlowSession(opts.flowId, `AI 帮我办理`);
    } else if (opts?.flowId && get().activeFlowId !== opts.flowId) {
      set({ activeFlowId: opts.flowId });
    }

    const userMessage: ChatMessage = {
      id: `user_${Date.now()}`,
      role: 'user',
      content: question,
      timestamp: new Date(),
    };
    addMessage(userMessage);

    const loadingId = `assistant_${Date.now()}`;
    addMessage({
      id: loadingId, role: 'assistant', content: '', timestamp: new Date(),
      isLoading: true, steps: [],
    });

    const abortController = new AbortController();
    set({ isLoading: true, error: null, abortController });

    const flowId = targetFlowId || undefined;
    const request: ChatRequest = {
      question,
      session_id: sessionId,
      flow_slots: get().flowSlots,
      ...(flowId ? { flow_id: flowId } : {}),
      ...(opts?.force ? { flow_force: true } : {}),
      ...(opts?.action ? { flow_action: opts.action } : {}),
      ...(opts?.intentResolved ? { intent_resolved: true } : {}),
      ...(get().definitionVersion ? { definition_version: get().definitionVersion } : {}),
    };

    const attachPayload = (payload: FlowPayload | null | undefined, messageId: string) => {
      if (!payload) return;
      get().applyFlowPayload(payload);
      set((state) => {
        const newMessages = state.messages.map((msg) =>
          msg.id === messageId ? { ...msg, ...payloadPair(payload) } : msg
        );
        saveMessages(sessionId, newMessages);
        return { messages: newMessages };
      });
    };

    const applyDone = (response: any, fromFallback = false) => {
      set((state) => {
        const newMessages = state.messages.map((msg) =>
          msg.id === loadingId
            ? {
                ...msg,
                content: response.answer,
                citations: response.citations,
                isLoading: false,
                currentStep: undefined,
                ...(response.flow_payload ? payloadPair(response.flow_payload) : {}),
              }
            : msg
        );
        saveMessages(sessionId, newMessages);
        const sessions = fromFallback ? state.sessions : state.sessions.map((s) =>
          s.id === sessionId ? { ...s, title: generateTitle(newMessages), lastActive: Date.now() } : s
        );
        saveSessions(sessions);
        return { messages: newMessages, isLoading: false, abortController: null, sessions };
      });

      const fid = response.flow_id || '';
      const version = Number(response.definition_version || 0);
      if (fid && response.flow_action !== 'choice') {
        saveFlowMeta(sessionId, { flowId: fid, version: version || get().definitionVersion });
        set({ activeFlowId: fid, ...(version ? { definitionVersion: version } : {}) });
      }
      if (response.intent_choice) {
        set({ pendingIntentChoice: response.intent_choice as IntentChoice });
      }
      if (response.flow_payload) {
        get().applyFlowPayload(response.flow_payload);
      } else if (response.flow_slots) {
        saveFlowSlots(sessionId, response.flow_slots);
        set(slotPair(response.flow_slots));
      }
    };

    try {
      await sendQuestionStream(
        request,
        abortController.signal,
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
        (response) => applyDone(response),
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
        {
          onToken: (token: string) => {
            set((state) => ({
              messages: state.messages.map((msg) =>
                msg.id === loadingId ? { ...msg, content: msg.content + token } : msg
              ),
            }));
          },
          onFlowPayload: (payload) => attachPayload(payload, loadingId),
          onIntentChoice: (choice) => set({ pendingIntentChoice: choice }),
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
      // 降级到普通请求（非流式同样携带通用结构化字段）
      try {
        const response = await sendQuestion(request, abortController.signal);
        applyDone(response, true);
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
      saveFlowSlots(newId, {});
      saveFlowMeta(newId, { flowId: '', version: 0 });
      return {
        messages: [], sessionId: newId, error: null, sessions,
        ...slotPair({}), ...payloadPair(null),
        activeFlowId: null, definitionVersion: 0, pendingIntentChoice: null,
      };
    });
  },

  createSession: () => {
    const newId = generateSessionId();
    set((state) => {
      const newSession: Session = { id: newId, title: '新对话', createdAt: Date.now(), lastActive: Date.now() };
      const sessions = [newSession, ...state.sessions];
      saveSessions(sessions);
      saveFlowSlots(newId, {});
      saveFlowMeta(newId, { flowId: '', version: 0 });
      return {
        messages: [], sessionId: newId, error: null, sessions,
        ...slotPair({}), ...payloadPair(null),
        activeFlowId: null, definitionVersion: 0, pendingIntentChoice: null,
      };
    });
    return newId;
  },

  switchSession: async (sessionId: string) => {
    get().cancelRequest();
    let messages = loadMessages(sessionId);
    let slots = loadFlowSlots(sessionId);
    const meta = loadFlowMeta(sessionId);
    const prev = get();
    // 先在 A 会话上传材料、再切回 B 会话继续填表是常见路径：
    // 同一流程下把刚上传但未绑定的附件 ID 一起带过去，既不要求重传，
    // 也不依赖模型是否主动调用 get_my_attachments。不同流程一律不带。
    const prevMeta = loadFlowMeta(prev.sessionId);
    const prevAtts = (prev.flowSlots.attachments || {}) as Record<string, string>;
    if (prev.sessionId !== sessionId && meta.flowId && prevMeta.flowId
        && meta.flowId === prevMeta.flowId && Object.keys(prevAtts).length > 0) {
      slots = {
        ...slots,
        attachments: { ...prevAtts, ...((slots.attachments || {}) as Record<string, string>) },
      };
      saveFlowSlots(sessionId, slots);
    }
    set({
      messages, sessionId, error: null, isLoading: false,
      ...slotPair(slots), ...payloadPair(null),
      activeFlowId: meta.flowId || null, definitionVersion: meta.version || 0,
      pendingIntentChoice: null,
    });
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
          messages = backendMessages;
          set({ messages: backendMessages });
        }
      } catch { /* ignore */ }
    }
    // 流程会话字段丢失 → 按该流程配置从聊天历史原文重抽（只读，不落库）
    if (meta.flowId && Object.keys(slots).length === 0) {
      slots = await reextractFlowSlots(sessionId, meta.flowId);
      saveFlowSlots(sessionId, slots);
      set(slotPair(slots));
    }
  },

  deleteSession: (sessionId: string) => {
    set((state) => {
      const sessions = state.sessions.filter((s) => s.id !== sessionId);
      deleteMessages(sessionId);
      saveSessions(sessions);
      if (state.sessionId === sessionId) {
        if (sessions.length > 0) {
          const meta = loadFlowMeta(sessions[0].id);
          return {
            messages: loadMessages(sessions[0].id), sessionId: sessions[0].id, sessions,
            ...slotPair(loadFlowSlots(sessions[0].id)), ...payloadPair(null),
            activeFlowId: meta.flowId || null, definitionVersion: meta.version || 0,
            pendingIntentChoice: null,
          };
        }
        const newId = generateSessionId();
        const newSession: Session = { id: newId, title: '新对话', createdAt: Date.now(), lastActive: Date.now() };
        saveSessions([newSession]);
        return {
          messages: [], sessionId: newId, sessions: [newSession],
          ...slotPair({}), ...payloadPair(null),
          activeFlowId: null, definitionVersion: 0, pendingIntentChoice: null,
        };
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
        if (!localIds.has(bs.id)) merged.push(bs);
      }
      merged.sort((a, b) => b.lastActive - a.lastActive);
      saveSessions(merged);
      return { sessions: merged, sessionsLoaded: true };
    });
  },

  setError: (error: string | null) => set({ error }),

  // ── 兼容别名（旧组件 / 旧 E2E 仍可调用）──
  setEntryAttachment: (kind, id) => get().setFlowAttachment(get().activeFlowId || 'driver_entry', kind, id),
  applyEntryPayload: (payload) => get().applyFlowPayload(payload),
  startEntrySession: () => get().startFlowSession('driver_entry', 'AI 帮我申请入园'),
}));
