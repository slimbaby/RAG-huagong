import axios from 'axios';

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api',
  timeout: 300000,
  headers: { 'Content-Type': 'application/json' },
});

apiClient.interceptors.request.use(
  (config) => config,
  (error) => Promise.reject(error)
);

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      const { status, data } = error.response;
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
