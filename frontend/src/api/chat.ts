import apiClient from './client';
import type { ChatRequest, ChatResponse, StepProgress } from '../types';

export async function sendQuestion(request: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  const response = await apiClient.post<ChatResponse>('/chat/query', request, { signal });
  return response.data;
}

export async function sendQuestionStream(
  request: ChatRequest,
  signal: AbortSignal,
  onStep: (step: StepProgress) => void,
  onDone: (response: ChatResponse) => void,
  onError: (error: string) => void,
): Promise<void> {
  const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api';
  const res = await fetch(`${baseURL}/chat/query/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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
          } else if (event.type === 'done') {
            onDone({
              task_id: event.task_id,
              trace_id: event.trace_id,
              answer: event.answer,
              citations: event.citations || [],
              optimization: event.optimization,
              timestamp: new Date().toISOString(),
            });
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
