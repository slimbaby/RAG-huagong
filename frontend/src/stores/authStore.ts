import { create } from 'zustand';

const TOKEN_KEY = 'rag_auth_token';
const USER_KEY = 'rag_auth_user';

export const getStoredToken = () => localStorage.getItem(TOKEN_KEY);

interface AuthState {
  token: string | null;
  username: string | null;
  initialized: boolean;
  setAuth: (token: string, username: string) => void;
  logout: () => void;
  init: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  username: null,
  initialized: false,
  setAuth: (token, username) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, username);
    set({ token, username, initialized: true });
  },
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    set({ token: null, username: null });
  },
  init: () => {
    set({
      token: localStorage.getItem(TOKEN_KEY),
      username: localStorage.getItem(USER_KEY),
      initialized: true,
    });
  },
}));
