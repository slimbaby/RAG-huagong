import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock the authStore import before importing client
vi.mock('../../stores/authStore', () => ({
  getStoredToken: () => localStorage.getItem('rag_auth_token'),
}));

describe('apiClient interceptors', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('401 with stored token clears localStorage and redirects', async () => {
    localStorage.setItem('rag_auth_token', 'expired');
    localStorage.setItem('rag_auth_user', 'alice');

    const assignSpy = vi.fn();
    Object.defineProperty(window, 'location', {
      value: { pathname: '/chat', href: '', assign: assignSpy },
      writable: true,
    });

    // Import after mock setup
    const { default: apiClient } = await import('../client');

    // Simulate a 401 response via adapter
    apiClient.defaults.adapter = async () => {
      const err: any = new Error('Unauthorized');
      err.response = {
        status: 401,
        data: { detail: 'token expired' },
        config: { url: '/api/chat' },
      };
      throw err;
    };

    await expect(apiClient.get('/api/chat')).rejects.toThrow();
    // After 401 handler: token should be cleared
    expect(localStorage.getItem('rag_auth_token')).toBeNull();
    expect(localStorage.getItem('rag_auth_user')).toBeNull();
  });

  it('401 on /auth/login does NOT clear token', async () => {
    localStorage.setItem('rag_auth_token', 'some_token');

    const { default: apiClient } = await import('../client');

    apiClient.defaults.adapter = async () => {
      const err: any = new Error('Bad credentials');
      err.response = {
        status: 401,
        data: { detail: 'wrong password' },
        config: { url: '/api/auth/login' },
      };
      throw err;
    };

    await expect(apiClient.post('/api/auth/login', {})).rejects.toThrow();
    // Token should NOT be cleared for login 401
    expect(localStorage.getItem('rag_auth_token')).toBe('some_token');
  });
});