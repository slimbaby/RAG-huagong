import { describe, it, expect, beforeEach } from 'vitest';
import { useAuthStore, getStoredToken } from '../authStore';

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({
    token: null,
    username: null,
    role_id: null,
    role_name: null,
    scope_ids: [],
    initialized: false,
  });
});

describe('authStore', () => {
  it('setAuth stores token + user in state and localStorage', () => {
    useAuthStore.getState().setAuth('tok123', 'alice', 'admin', '管理员', ['all']);
    const s = useAuthStore.getState();
    expect(s.token).toBe('tok123');
    expect(s.username).toBe('alice');
    expect(s.role_id).toBe('admin');
    expect(getStoredToken()).toBe('tok123');
    expect(localStorage.getItem('rag_auth_user')).toBe('alice');
  });

  it('logout clears state and localStorage', () => {
    useAuthStore.getState().setAuth('tok', 'bob');
    useAuthStore.getState().logout();
    const s = useAuthStore.getState();
    expect(s.token).toBeNull();
    expect(s.username).toBeNull();
    expect(getStoredToken()).toBeNull();
  });

  it('init with no stored token sets initialized=true with nulls', async () => {
    await useAuthStore.getState().init();
    const s = useAuthStore.getState();
    expect(s.initialized).toBe(true);
    expect(s.token).toBeNull();
  });

  it('init with stored token but no /me API falls back to cache', async () => {
    localStorage.setItem('rag_auth_token', 'cached_tok');
    localStorage.setItem('rag_auth_user', 'cached_user');
    await useAuthStore.getState().init();
    const s = useAuthStore.getState();
    expect(s.token).toBe('cached_tok');
    expect(s.username).toBe('cached_user');
    expect(s.initialized).toBe(true);
  });
});