import apiClient from './client';
import type { ChatRequest, ChatResponse, StepProgress, EntryPayload, FlowPayload, IntentChoice } from '../types';
import { getStoredToken } from '../stores/authStore';

export async function sendQuestion(request: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  const response = await apiClient.post<ChatResponse>('/chat/query', request, { signal });
  return response.data;
}

export interface StreamCallbacks {
  onToken?: (content: string) => void;
  onFlowPayload?: (payload: FlowPayload, flowId?: string) => void;
  onIntentChoice?: (choice: IntentChoice) => void;
  /** @deprecated 仅旧前端使用；新链路统一走 onFlowPayload */
  onEntryPayload?: (payload: EntryPayload) => void;
}

/**
 * SSE 通道。判定规则全在后端：前端只解析 intent_choice / flow_payload 并渲染。
 * 兼容旧 entry_payload 事件，但同一轮只回调一次结构化产物，避免重复落状态。
 */
export async function sendQuestionStream(
  request: ChatRequest,
  signal: AbortSignal,
  onStep: (step: StepProgress) => void,
  onDone: (response: ChatResponse) => void,
  onError: (error: string) => void,
  callbacks: StreamCallbacks | ((content: string) => void) = {},
  legacyOnEntryPayload?: (payload: EntryPayload) => void,
): Promise<void> {
  // 兼容旧调用签名 (onToken, onEntryPayload)
  const cb: StreamCallbacks = typeof callbacks === 'function'
    ? { onToken: callbacks, onEntryPayload: legacyOnEntryPayload }
    : callbacks;

  const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api';
  const token = getStoredToken();
  const res = await fetch(`${baseURL}/chat/query/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(request),
    signal,
  });

  if (!res.ok) {
    onError(`HTTP ${res.status}`);
    return;
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let payloadDelivered = false;

  const deliverPayload = (payload: FlowPayload | EntryPayload | null | undefined, flowId?: string) => {
    if (!payload || payloadDelivered) return;
    payloadDelivered = true;
    if (cb.onFlowPayload) cb.onFlowPayload(payload as FlowPayload, flowId);
    else cb.onEntryPayload?.(payload as EntryPayload);
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;

        try {
          const event = JSON.parse(trimmed.slice(6));
          if (event.type === 'step') {
            onStep({
              node: event.node,
              label: event.node,
              status: event.status,
              data: event.data,
            });
          } else if (event.type === 'token') {
            cb.onToken?.(event.content);
          } else if (event.type === 'flow_payload') {
            deliverPayload(event.payload as FlowPayload, event.flow_id);
          } else if (event.type === 'entry_payload') {
            // 后端在通用链路里会同时推两个兼容事件；已处理过就不再重复
            if (!cb.onFlowPayload) deliverPayload(event.payload as EntryPayload);
          } else if (event.type === 'intent_choice') {
            cb.onIntentChoice?.({
              candidates: event.candidates || [],
              hint: event.hint || '',
              question: event.question || request.question,
              flow_id: event.flow_id,
            });
          } else if (event.type === 'done') {
            const flowPayload = (event.flow_payload || event.entry_payload || null) as FlowPayload | null;
            onDone({
              task_id: event.task_id,
              trace_id: event.trace_id,
              answer: event.answer,
              citations: event.citations || [],
              optimization: event.optimization,
              entry_action: Boolean(event.entry_action),
              entry_slots: event.entry_slots,
              entry_payload: event.entry_payload || null,
              flow_id: event.flow_id,
              flow_action: event.flow_action,
              flow_slots: event.flow_slots,
              flow_payload: flowPayload,
              intent_choice: event.intent_choice
                ? {
                    candidates: event.intent_choice.candidates || [],
                    hint: event.intent_choice.hint || '',
                    question: event.intent_choice.question || request.question,
                    flow_id: event.intent_choice.flow_id,
                  }
                : null,
              definition_version: event.definition_version || 0,
              timestamp: new Date().toISOString(),
            });
            // done 是降级兜底：若流中没有单独的 payload 事件，也要落状态
            deliverPayload(flowPayload, event.flow_id);
          } else if (event.type === 'error') {
            onError(event.message || '未知错误');
          }
        } catch {
          // skip malformed JSON
        }
      }
    }
  } catch (e: any) {
    if (e.name !== 'AbortError') {
      onError(e.message || '流式读取失败');
    }
  }
}

export async function getChatHistory(sessionId: string, limit: number = 20) {
  const response = await apiClient.get(`/chat/history/${sessionId}`, { params: { limit } });
  return response.data;
}

export async function clearChatHistory(sessionId: string) {
  const response = await apiClient.delete(`/chat/history/${sessionId}`);
  return response.data;
}

export async function getSessions(limit: number = 50) {
  const response = await apiClient.get('/chat/sessions', { params: { limit } });
  return response.data;
}

export async function getSessionHistory(sessionId: string, limit: number = 60) {
  const response = await apiClient.get(`/chat/history/${sessionId}`, { params: { limit } });
  return response.data;
}
