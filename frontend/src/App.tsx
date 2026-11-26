import React, { Suspense, useEffect } from 'react';
import { ConfigProvider, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Sidebar from './components/Layout/Sidebar';
import ErrorBoundary from './components/ErrorBoundary';
import { useAuthStore } from './stores/authStore';

const ChatPage = React.lazy(() => import('./pages/ChatPage'));
const DocumentPage = React.lazy(() => import('./pages/DocumentPage'));
const RecordPage = React.lazy(() => import('./pages/RecordPage'));
const EvaluationPage = React.lazy(() => import('./pages/EvaluationPage'));
const LoginPage = React.lazy(() => import('./pages/LoginPage'));
const FlowApplyPage = React.lazy(() => import('./pages/FlowApplyPage'));
const FlowMyPage = React.lazy(() => import('./pages/FlowMyPage'));
const FlowReviewPage = React.lazy(() => import('./pages/FlowReviewPage'));
const FlowConfigPage = React.lazy(() => import('./pages/FlowConfigPage'));

const PageLoader = () => (
  <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
    <Spin size="large" />
  </div>
);

const RequireAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { token, initialized } = useAuthStore();
  if (!initialized) return <PageLoader />;
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
};

// 老 URL 迁移兼容：仅这一处保留流程标识，通用页面完全由 /flows/:flowId/* 接管。
const LEGACY_REDIRECTS: Record<string, string> = {
  '/entry/apply': '/flows/driver_entry/apply',
  '/entry/my': '/flows/driver_entry/my',
  '/entry/review': '/flows/driver_entry/review',
};

const MainLayout: React.FC = () => (
  <div style={{ display: 'flex', width: '100%', height: '100vh', overflow: 'hidden' }}>
    <Sidebar />
    <main style={{
      flex: 1,
      background: '#070C1A',
      overflow: 'auto',
      display: 'flex',
      flexDirection: 'column',
    }}>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/documents" element={<DocumentPage />} />
          <Route path="/records" element={<RecordPage />} />
          <Route path="/evaluation" element={<EvaluationPage />} />
          {/* 配置后台（静态路径放在动态段之前） */}
          <Route path="/flows/config" element={<FlowConfigPage />} />
          {/* 通用流程页面：新增流程不需要改这里 */}
          <Route path="/flows/:flowId/apply" element={<FlowApplyPage />} />
          <Route path="/flows/:flowId/my" element={<FlowMyPage />} />
          <Route path="/flows/:flowId/review" element={<FlowReviewPage />} />
          {Object.entries(LEGACY_REDIRECTS).map(([from, to]) => (
            <Route key={from} path={from} element={<Navigate to={to} replace />} />
          ))}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </main>
  </div>
);

const App: React.FC = () => {
  const initAuth = useAuthStore((s) => s.init);
  useEffect(() => {
    initAuth();
  }, [initAuth]);

  return (
    <ErrorBoundary>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: '#4F8CFF',
            colorBgContainer: '#0D1525',
            colorBgElevated: '#101A2E',
            colorText: '#E4EBF5',
            colorTextSecondary: '#94A3B8',
            colorBorder: '#1A2748',
            borderRadius: 8,
            fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          },
          components: {
            Table: {
              headerBg: '#0D1525',
              headerColor: '#5E6E88',
              rowHoverBg: 'rgba(15, 25, 45, 0.6)',
              borderColor: '#1A2748',
            },
          },
        }}
      >
        <BrowserRouter>
          <Suspense fallback={<PageLoader />}>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route
                path="/*"
                element={
                  <RequireAuth>
                    <MainLayout />
                  </RequireAuth>
                }
              />
            </Routes>
          </Suspense>
        </BrowserRouter>
      </ConfigProvider>
    </ErrorBoundary>
  );
};

export default App;
