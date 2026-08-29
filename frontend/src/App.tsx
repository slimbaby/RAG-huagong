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
