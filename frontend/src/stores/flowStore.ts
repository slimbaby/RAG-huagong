/**
 * 动态菜单与渲染元数据缓存（D8 / D10）。
 *
 * 前端不持有任何流程定义、字段清单或判定规则：
 * 菜单入口、表单字段、附件类型、状态显示名全部来自后端已发布配置。
 * 新增流程时这里的代码与文件都不需要改动。
 */
import { create } from 'zustand';
import { getEntryPoints, getRenderConfig } from '../api/flow';
import type { FlowEntryPoint, FlowRenderConfig } from '../api/flow';

interface FlowState {
  entryPoints: FlowEntryPoint[];
  loaded: boolean;
  roleId: string;
  configs: Record<string, FlowRenderConfig>;
  errors: Record<string, string>;
  loadEntryPoints: (force?: boolean) => Promise<void>;
  loadConfig: (flowId: string, force?: boolean) => Promise<FlowRenderConfig | null>;
  menuItems: () => FlowEntryPoint[];
  shortcutItems: () => FlowEntryPoint[];
  attachmentKinds: (flowId?: string) => FlowRenderConfig['attachments'];
}

export const useFlowStore = create<FlowState>((set, get) => ({
  entryPoints: [],
  loaded: false,
  roleId: '',
  configs: {},
  errors: {},

  loadEntryPoints: async (force = false) => {
    if (get().loaded && !force) return;
    try {
      const res = await getEntryPoints();
      set({ entryPoints: res.entry_points || [], roleId: res.role_id || '', loaded: true });
      // 配置改版后菜单可能变化，强制刷新时一并清掉渲染配置缓存
      if (force) set({ configs: {} });
    } catch {
      // 后端未就绪时保留已有菜单，不阻塞主界面
      set({ loaded: true });
    }
  },

  loadConfig: async (flowId: string, force = false) => {
    if (!flowId) return null;
    if (!force && get().configs[flowId]) return get().configs[flowId];
    try {
      const cfg = await getRenderConfig(flowId);
      set((s) => ({ configs: { ...s.configs, [flowId]: cfg }, errors: { ...s.errors, [flowId]: '' } }));
      return cfg;
    } catch (e: any) {
      set((s) => ({
        errors: { ...s.errors, [flowId]: e?.response?.data?.detail || '流程配置加载失败' },
      }));
      return null;
    }
  },

  menuItems: () => get().entryPoints.filter((e) => e.type === 'menu'),
  shortcutItems: () => get().entryPoints.filter((e) => e.type === 'shortcut'),

  attachmentKinds: (flowId?: string) => {
    const { configs, entryPoints } = get();
    const id = flowId || entryPoints.find((e) => e.type === 'shortcut')?.flow_id || entryPoints[0]?.flow_id;
    return (id && configs[id]?.attachments) || [];
  },
}));

/** 确认卡预填：键名与 flow_id 绑定，避免不同流程之间串字段。 */
export const prefillKey = (flowId: string) => `flow_prefill_slots:${flowId}`;
