import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../stores/authStore', () => ({ getStoredToken: () => 'test-token' }));

import { sendQuestionStream } from '../chat';

const makeResponse = (events: any[]) => {
  const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
  const encoder = new TextEncoder();
  let sent = false;
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined };
          sent = true;
          return { done: false, value: encoder.encode(body) };
        },
      }),
    },
  } as any;
};

const noop = () => {};
const request = { question: '帮我申请入园', session_id: 'flow-1' };

beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
afterEach(() => { vi.unstubAllGlobals(); });

describe('SSE 通用事件解析（前端只渲染，不判定）', () => {
  it('flow_payload 与兼容 entry_payload 同一轮只回调一次', async () => {
    const payload = {
      __flow_payload__: true, __entry_payload__: true,
      flow_id: 'driver_entry', definition_version: 2,
      slots: { driver_name: '张三' }, missing: [], errors: [], complete: false, card: null,
    };
    vi.mocked(fetch).mockResolvedValue(makeResponse([
      { type: 'token', content: '好的' },
      { type: 'flow_payload', payload, flow_id: 'driver_entry' },
      { type: 'entry_payload', payload },
      { type: 'done', task_id: 't1', answer: '已记录', citations: [],
        flow_id: 'driver_entry', flow_action: 'action', flow_payload: payload,
        definition_version: 2 },
    ]));

    const onFlowPayload = vi.fn();
    const onEntryPayload = vi.fn();
    const onDone = vi.fn();
    const onToken = vi.fn();
    await sendQuestionStream(request, new AbortController().signal, noop, onDone, noop,
      { onToken, onFlowPayload, onEntryPayload });

    expect(onToken).toHaveBeenCalledWith('好的');
    expect(onFlowPayload).toHaveBeenCalledTimes(1);
    expect(onFlowPayload).toHaveBeenCalledWith(payload, 'driver_entry');
    expect(onEntryPayload).not.toHaveBeenCalled();
    expect(onDone.mock.calls[0][0]).toMatchObject({
      flow_id: 'driver_entry', flow_action: 'action', definition_version: 2,
      flow_payload: payload,
    });
  });

  it('intent_choice 事件与 done 都携带后端下发的候选', async () => {
    const choice = {
      type: 'intent_choice', candidates: [{ flow_id: 'driver_entry', name: '入园申请', action: 'action' }],
      hint: '请确认', question: '能帮我申请入园吗？', flow_id: 'driver_entry',
    };
    vi.mocked(fetch).mockResolvedValue(makeResponse([
      choice,
      { type: 'done', task_id: 't2', answer: '请确认', citations: [],
        intent_choice: choice, flow_action: 'choice', flow_id: 'driver_entry' },
    ]));

    const onIntentChoice = vi.fn();
    const onDone = vi.fn();
    await sendQuestionStream(request, new AbortController().signal, noop, onDone, noop,
      { onIntentChoice });

    expect(onIntentChoice).toHaveBeenCalledTimes(1);
    expect(onIntentChoice.mock.calls[0][0].candidates).toHaveLength(1);
    expect(onDone.mock.calls[0][0].intent_choice.candidates).toHaveLength(1);
  });

  it('done 兜底：没有单独 payload 事件时也要把结构化结果交给调用方', async () => {
    const payload = { slots: { phone: '13800138000' }, missing: [], errors: [], complete: false };
    vi.mocked(fetch).mockResolvedValue(makeResponse([
      { type: 'done', task_id: 't3', answer: '已记录', citations: [],
        flow_id: 'driver_entry', flow_payload: payload },
    ]));
    const onFlowPayload = vi.fn();
    await sendQuestionStream(request, new AbortController().signal, noop, noop, noop, { onFlowPayload });
    expect(onFlowPayload).toHaveBeenCalledTimes(1);
  });
});
