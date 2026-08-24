import React from 'react';

interface Props { children: React.ReactNode; }
interface State { hasError: boolean; error: Error | null; }

class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          height: '100vh', background: '#070C1A', color: '#E4EBF5',
          fontFamily: "'Inter', sans-serif", padding: 40, textAlign: 'center',
        }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>⚠️</div>
          <h2 style={{ fontSize: 20, fontWeight: 600, marginBottom: 8 }}>页面出现错误</h2>
          <p style={{ color: '#94A3B8', fontSize: 14, marginBottom: 24, maxWidth: 500 }}>
            {this.state.error?.message || '发生了未知错误'}
          </p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '10px 24px', background: 'linear-gradient(-90deg, #3B6FE8, #6366F1)',
              border: 'none', borderRadius: 8, color: '#fff', fontSize: 14, fontWeight: 600,
              cursor: 'pointer', fontFamily: "'Inter', sans-serif",
            }}
          >
            刷新页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
