import axios from 'axios';
import { getStoredToken } from '../stores/authStore';

const TOKEN_KEY = 'rag_auth_token';
const USER_KEY = 'rag_auth_user';

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api',
  timeout: 300000,
  headers: { 'Content-Type': 'application/json' },
});

apiClient.interceptors.request.use(
  (config) => {
    const token = getStoredToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      const { status, data, config } = error.response;
      // 携带了 token 但认证失败 = token 过期/失效，清除并跳转登录页。
      // 登录接口本身的 401（密码错误）不触发跳转。
      if (status === 401 && getStoredToken() && !config.url?.includes('/auth/login')) {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        if (!window.location.pathname.startsWith('/login')) {
          window.location.href = '/login';
        }
      }
      switch (status) {
        case 400: console.error('请求参数错误:', data); break;
        case 401: console.error('未授权:', data); break;
        case 404: console.error('资源不存在:', data); break;
        case 500: console.error('服务器错误:', data); break;
        default: console.error(`请求失败 (${status}):`, data);
      }
    } else if (error.request) {
      console.error('网络错误，请检查网络连接');
    } else {
      console.error('请求配置错误:', error.message);
    }
    return Promise.reject(error);
  }
);

export default apiClient;
