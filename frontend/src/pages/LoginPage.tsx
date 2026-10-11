import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Input, Form, message } from 'antd';
import { ExperimentOutlined, LockOutlined, UserOutlined } from '@ant-design/icons';
import { login, register } from '../api/auth';
import { useAuthStore } from '../stores/authStore';

const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const res = mode === 'login'
        ? await login(values.username, values.password)
        : await register(values.username, values.password);
      // 先存token，再用token拉/me拿角色信息
      setAuth(res.access_token, res.username);
      const { getMe } = await import('../api/auth');
      const me = await getMe();
      setAuth(res.access_token, res.username, me.role_id, me.role_name, me.scope_ids);
      message.success(mode === 'login' ? '登录成功' : '账户创建成功');
      navigate('/', { replace: true });
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      if (mode === 'register' && e?.response?.status === 403) {
        message.error(detail || '注册已关闭，请直接登录');
        setMode('login');
      } else {
        message.error(detail || (mode === 'login' ? '登录失败' : '注册失败'));
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <div style={styles.logoRow}>
          <ExperimentOutlined style={{ fontSize: 28, color: '#4F8CFF' }} />
          <span style={styles.title}>化工园区RAG知识库系统</span>
        </div>

        <Form onFinish={handleSubmit} layout="vertical" requiredMark={false}>
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input prefix={<UserOutlined style={{ color: '#5E6E88' }} />} placeholder="用户名" size="large" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[
              { required: true, message: '请输入密码' },
              ...(mode === 'register' ? [{ min: 6, message: '密码至少 6 位' }] : []),
            ]}
          >
            <Input.Password prefix={<LockOutlined style={{ color: '#5E6E88' }} />} placeholder="密码" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block loading={loading}>
            {mode === 'login' ? '登 录' : '创建账户'}
          </Button>
        </Form>

        <div style={styles.switchRow} onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
          {mode === 'login' ? '首次使用？创建管理员账户' : '已有账户？返回登录'}
        </div>
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    width: '100%',
    height: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'radial-gradient(ellipse at 50% 30%, #0D1525 0%, #070C1A 70%)',
  },
  card: {
    width: 380,
    padding: '40px 36px',
    borderRadius: 12,
    background: '#0D1525',
    border: '1px solid #1A2748',
    boxShadow: '0 8px 40px rgba(0, 0, 0, 0.4)',
  },
  logoRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    marginBottom: 32,
  },
  title: {
    fontSize: 17,
    fontWeight: 700,
    color: '#E4EBF5',
    fontFamily: "'Inter', sans-serif",
  },
  switchRow: {
    marginTop: 20,
    textAlign: 'center',
    fontSize: 13,
    color: '#5E6E88',
    cursor: 'pointer',
    userSelect: 'none',
  },
};

export default LoginPage;
