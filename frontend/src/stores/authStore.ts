import { create } from 'zustand';

const TOKEN_KEY = 'rag_auth_token';
const USER_KEY = 'rag_auth_user';
const ROLE_KEY = 'rag_auth_role';

export const getStoredToken = () => localStorage.getItem(TOKEN_KEY);

interface AuthState {
  token: string | null;
  username: string | null;
  role_id: string | null;
  role_name: string | null;
  scope_ids: string[];
  initialized: boolean;
  setAuth: (token: string, username: string, roleId?: string, roleName?: string, scopeIds?: string[]) => void;
  logout: () => void;
  init: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  username: null,
  role_id: null,
  role_name: null,
  scope_ids: [],
  initialized: false,
  setAuth: (token, username, roleId = 'admin', roleName = '管理员', scopeIds = ['all']) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, username);
    localStorage.setItem(ROLE_KEY, JSON.stringify({ role_id: roleId, role_name: roleName, scope_ids: scopeIds }));
    set({ token, username, role_id: roleId, role_name: roleName, scope_ids: scopeIds, initialized: true });
  },
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ROLE_KEY);
    set({ token: null, username: null, role_id: null, role_name: null, scope_ids: [], initialized: true });
  },
  init: async () => {
    const token = localStorage.getItem(TOKEN_KEY);
    const username = localStorage.getItem(USER_KEY);
    if (!token || !username) {
      set({ token: null, username: null, initialized: true });
      return;
    }
    // 从 /me 拉最新角色信息（admin 改角色后立即生效）
    try {
      const { getMe } = await import('../api/auth');
      const me = await getMe();
      set({
        token,
        username: me.username,
        role_id: me.role_id,
        role_name: me.role_name,
        scope_ids: me.scope_ids,
        initialized: true,
      });
    } catch {
      // /me 失败时降级读 localStorage 缓存
      try {
        const cached = localStorage.getItem(ROLE_KEY);
        const parsed = cached ? JSON.parse(cached) : { role_id: 'admin', role_name: '管理员', scope_ids: ['all'] };
        set({
          token,
          username,
          role_id: parsed.role_id || 'admin',
          role_name: parsed.role_name || '管理员',
          scope_ids: parsed.scope_ids || ['all'],
          initialized: true,
        });
      } catch {
        set({ token, username, role_id: 'admin', role_name: '管理员', scope_ids: ['all'], initialized: true });
      }
    }
  },
}));
