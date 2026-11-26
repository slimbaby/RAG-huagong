import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../api/chat', () => ({
  sendQuestion: vi.fn(),
  sendQuestionStream: vi.fn(async () => {}),
  getSessions: vi.fn(async () => ({ sessions: [] })),
  getSessionHistory: vi.fn(async () => ({ history: [] })),
}));
vi.mock('../../api/flow', () => ({
  parseFlowText: vi.fn(async () => ({ slots: {} })),
}));

import { useChatStore } from '../chatStore';
import { sendQuestionStream } from '../../api/chat';

const AMBIGUOUS = '能帮我申请入园吗？';
const CHOICE = {
  question: AMBIGUOUS,
  hint: '请确认要办理哪个流程',
  candidates: [{ flow_id: 'driver_entry', name: '司机入园预约', action: 'action' }],
};

const lastRequest = () =>
  (sendQuestionStream as unknown as { mock: { calls: any[][] } }).mock.calls.at(-1)?.[0];
const lastCallbacks = () =>
  (sendQuestionStream as unknown as { mock: { calls: any[][] } }).mock.calls.at(-1)?.[5];

const reset = () => {
  useChatStore.setState({
    pendingIntentChoice: null,
    messages: [],
    isLoading: false,
    error: null,
    sessionId: 'session_plain',
    flowSlots: {},
    entrySlots: {},
    flowPayload: null,
    entryPayload: null,
    activeFlowId: null,
    definitionVersion: 0,
    sessions: [],
    sessionsLoaded: true,
  });
};

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  reset();
});

describe('后端权威意图路由（前端零规则）', () => {
  it('两可句不再由前端挂起，直接交给后端判定', async () => {
    await useChatStore.getState().sendMessage(AMBIGUOUS);
    expect(sendQuestionStream).toHaveBeenCalledTimes(1);
    expect(useChatStore.getState().pendingIntentChoice).toBeNull();
    const req = lastRequest();
    expect(req.question).toBe(AMBIGUOUS);
    expect(req.flow_force).toBeUndefined();
    expect(req.flow_slots).toEqual({});
  });

  it('后端 intent_choice 事件只渲染候选，不触发新的请求', async () => {
    await useChatStore.getState().sendMessage(AMBIGUOUS);
    lastCallbacks().onIntentChoice(CHOICE);
    expect(useChatStore.getState().pendingIntentChoice).toEqual(CHOICE);
    expect(sendQuestionStream).toHaveBeenCalledTimes(1);
  });

  it('点「办理」带 flow_id + flow_force + intent_resolved，并切独立流程会话', async () => {
    await useChatStore.getState().sendMessage(AMBIGUOUS);
    lastCallbacks().onIntentChoice(CHOICE);
    await useChatStore.getState().resolveIntentChoice(CHOICE.candidates[0]);

    expect(sendQuestionStream).toHaveBeenCalledTimes(2);
    const req = lastRequest();
    expect(req.question).toBe(AMBIGUOUS);
    expect(req.flow_id).toBe('driver_entry');
    expect(req.flow_force).toBe(true);
    expect(req.flow_action).toBe('action');
    expect(req.intent_resolved).toBe(true);
    expect(req.session_id.startsWith('flow-')).toBe(true);
    expect(useChatStore.getState().pendingIntentChoice).toBeNull();
  });

  it('点「咨询」按原样发一次，只读动作且不强制办理', async () => {
    await useChatStore.getState().sendMessage(AMBIGUOUS);
    lastCallbacks().onIntentChoice(CHOICE);
    await useChatStore.getState().resolveIntentChoice(null);

    const req = lastRequest();
    expect(req.question).toBe(AMBIGUOUS);
    expect(req.flow_action).toBe('readonly');
    expect(req.intent_resolved).toBe(true);
    expect(req.flow_force).toBeUndefined();
    expect(req.session_id.startsWith('flow-')).toBe(false);
    expect(useChatStore.getState().pendingIntentChoice).toBeNull();
  });

  it('flow_payload 事件把槽位与配置版本落到会话状态', async () => {
    await useChatStore.getState().sendMessage('帮我申请入园，我叫张三');
    const payload = {
      __flow_payload__: true,
      flow_id: 'driver_entry',
      definition_version: 3,
      slots: { driver_name: '张三' },
      missing: ['phone'], errors: [], complete: false, card: null,
    };
    lastCallbacks().onFlowPayload(payload, 'driver_entry');
    expect(useChatStore.getState().flowSlots).toEqual({ driver_name: '张三' });
    expect(useChatStore.getState().activeFlowId).toBe('driver_entry');
    expect(useChatStore.getState().definitionVersion).toBe(3);
  });

  it('done 事件携带 flow_id 时锁定会话的流程版本', async () => {
    await useChatStore.getState().sendMessage('帮我申请入园');
    const onDone = (sendQuestionStream as unknown as { mock: { calls: any[][] } }).mock.calls.at(-1)?.[3];
    onDone({
      task_id: 't1', answer: '还缺手机号', citations: [],
      flow_id: 'driver_entry', flow_action: 'action',
      definition_version: 7, flow_slots: { driver_name: '张三' },
    });
    expect(useChatStore.getState().activeFlowId).toBe('driver_entry');
    expect(useChatStore.getState().definitionVersion).toBe(7);
    expect(useChatStore.getState().flowSlots).toEqual({ driver_name: '张三' });
  });
});

describe('会话切换不丢已收集字段（回归：上传图片后信息全没了）', () => {
  const fields = { driver_name: '张三', phone: '13800138000', plate_no: '鲁A12345' };
  const seedFlow = () => useChatStore.setState({
    flowSlots: { ...fields }, entrySlots: { ...fields },
    activeFlowId: 'driver_entry', definitionVersion: 1,
    flowPayload: { missing: ['车头照片'] } as any,
  });

  it('上传附件不新建会话，字段 / 版本 / 附件 ID 全在当前会话', () => {
    seedFlow();
    const before = useChatStore.getState().sessionId;
    useChatStore.getState().setFlowAttachment('driver_entry', 'vehicle_front', 'ATT_TEST');

    const s = useChatStore.getState();
    expect(s.sessionId).toBe(before);
    expect(s.sessionId.startsWith('flow-')).toBe(false);
    expect(s.flowSlots).toEqual({ ...fields, attachments: { vehicle_front: 'ATT_TEST' } });
    expect(s.definitionVersion).toBe(1);
    expect(s.activeFlowId).toBe('driver_entry');
    expect(JSON.parse(localStorage.getItem(`rag_flow_slots_${before}`) || '{}'))
      .toEqual({ ...fields, attachments: { vehicle_front: 'ATT_TEST' } });
    expect(s.messages).toEqual([]); // 本用例没有发消息，历史保持原样
  });

  it('连续上传 5 类附件只累加，不互相覆盖', () => {
    seedFlow();
    const kinds = ['vehicle_front', 'vehicle_rear', 'vehicle_license', 'driving_license', 'hazmat_license'];
    kinds.forEach((k, i) => useChatStore.getState().setFlowAttachment('driver_entry', k, `ATT_${i}`));
    const s = useChatStore.getState();
    expect(Object.keys((s.flowSlots.attachments || {}) as object)).toHaveLength(5);
    expect(s.flowSlots.driver_name).toBe('张三');
  });

  it('同一流程切独立会话时带走槽位与版本，聊天历史仍隔离', () => {
    seedFlow();
    const newId = useChatStore.getState().startFlowSession('driver_entry', 'AI 帮我办理');
    const s = useChatStore.getState();
    expect(s.sessionId).toBe(newId);
    expect(newId.startsWith('flow-')).toBe(true);
    expect(s.flowSlots.driver_name).toBe('张三');
    expect(s.definitionVersion).toBe(1);
    expect(s.messages).toEqual([]);
    expect(JSON.parse(localStorage.getItem(`rag_flow_slots_${newId}`) || '{}'))
      .toEqual({ ...fields });
  });

  it('切到另一个流程时清空旧流程字段，避免串单', () => {
    seedFlow();
    useChatStore.getState().startFlowSession('warehouse_in');
    const s = useChatStore.getState();
    expect(s.flowSlots).toEqual({});
    expect(s.activeFlowId).toBe('warehouse_in');
    expect(s.definitionVersion).toBe(0);
  });

  it('两可句前已收集字段，点「办理」后新会话仍带完整 flow_slots', async () => {
    await useChatStore.getState().sendMessage('帮我申请入园，我叫张三');
    lastCallbacks().onFlowPayload({
      __flow_payload__: true, flow_id: 'driver_entry', definition_version: 1,
      slots: { driver_name: '张三' }, missing: ['phone'], errors: [],
      complete: false, card: null,
    });
    lastCallbacks().onIntentChoice(CHOICE);
    await useChatStore.getState().resolveIntentChoice(CHOICE.candidates[0]);

    const req = lastRequest();
    expect(req.session_id.startsWith('flow-')).toBe(true);
    expect(req.flow_slots.driver_name).toBe('张三');
    expect(req.definition_version).toBe(1);
  });

  it('从上传附件的会话切回原会话：字段与新附件 ID 同时保留', async () => {
    localStorage.setItem('rag_flow_slots_session_plain', JSON.stringify(fields));
    localStorage.setItem('rag_flow_meta_session_plain',
      JSON.stringify({ flowId: 'driver_entry', version: 1 }));
    localStorage.setItem('rag_flow_meta_flow_upload',
      JSON.stringify({ flowId: 'driver_entry', version: 1 }));
    useChatStore.setState({
      sessionId: 'flow_upload',
      flowSlots: { attachments: { vehicle_front: 'ATT_UPLOADED' } },
      entrySlots: { attachments: { vehicle_front: 'ATT_UPLOADED' } },
      activeFlowId: 'driver_entry', definitionVersion: 1,
    });

    await useChatStore.getState().switchSession('session_plain');
    const s = useChatStore.getState();
    expect(s.sessionId).toBe('session_plain');
    expect(s.flowSlots.driver_name).toBe('张三');
    expect((s.flowSlots.attachments as Record<string, string>).vehicle_front).toBe('ATT_UPLOADED');
  });

  it('提交成功后清流程状态：历史核对卡撤下，后续问答不再带 flow_id', async () => {
    const card = { title: '入园申请信息核对', groups: [], attachments: [] };
    const payload: any = {
      flow_id: 'driver_entry', definition_version: 1,
      slots: { driver_name: '张三' }, missing: [], errors: [], complete: true, card,
    };
    reset();
    useChatStore.setState({
      sessionId: 'session_plain',
      flowSlots: { driver_name: '张三' }, entrySlots: { driver_name: '张三' },
      activeFlowId: 'driver_entry', definitionVersion: 1,
      flowPayload: payload, entryPayload: payload,
      messages: [
        { id: 'u1', role: 'user', content: '材料齐了', timestamp: new Date() },
        { id: 'a1', role: 'assistant', content: '请核对', timestamp: new Date(),
          flowPayload: payload, entryPayload: payload },
      ],
    });

    useChatStore.getState().clearFlowState();
    const s = useChatStore.getState();
    expect(s.activeFlowId).toBeNull();
    expect(s.flowSlots).toEqual({});
    expect(s.flowPayload).toBeNull();
    expect(s.messages.every((m: any) => !m.flowPayload && !m.entryPayload)).toBe(true);
    const stored = JSON.parse(localStorage.getItem('rag_chat_messages_session_plain') || '[]');
    expect(stored.every((m: any) => !m.flowPayload && !m.entryPayload)).toBe(true);

    await useChatStore.getState().sendMessage('今天天气真好啊');
    const req = lastRequest();
    expect(req.flow_id).toBeUndefined();
    expect(req.flow_slots).toEqual({});
  });

  it('切到另一个流程的会话时不带入旧流程附件', async () => {
    localStorage.setItem('rag_flow_slots_other', JSON.stringify({ item_name: '入库' }));
    localStorage.setItem('rag_flow_meta_other',
      JSON.stringify({ flowId: 'warehouse_in', version: 1 }));
    localStorage.setItem('rag_flow_meta_session_plain',
      JSON.stringify({ flowId: 'driver_entry', version: 1 }));
    useChatStore.setState({
      sessionId: 'session_plain',
      flowSlots: { attachments: { vehicle_front: 'ATT_UPLOADED' } },
      entrySlots: { attachments: { vehicle_front: 'ATT_UPLOADED' } },
      activeFlowId: 'driver_entry', definitionVersion: 1,
    });

    await useChatStore.getState().switchSession('other');
    expect(useChatStore.getState().flowSlots.attachments).toBeUndefined();
    expect(useChatStore.getState().flowSlots.item_name).toBe('入库');
  });
});
