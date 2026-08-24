import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { MessageSquare, FileText, ScrollText, FlaskConical, Plus, Trash2, MessageCircle, BarChart3 } from 'lucide-react';
import { useChatStore } from '../../stores/chatStore';

interface NavItem { key: string; path: string; label: string; icon: React.ReactNode; }

const navItems: NavItem[] = [
  { key: 'chat', path: '/', label: '知识库助手', icon: <MessageSquare size={18} /> },
  { key: 'documents', path: '/documents', label: '文档管理', icon: <FileText size={18} /> },
  { key: 'records', path: '/records', label: '调用记录', icon: <ScrollText size={18} /> },
  { key: 'evaluation', path: '/evaluation', label: '质量评测', icon: <BarChart3 size={18} /> },
];

const Sidebar: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { sessions, sessionId, createSession, switchSession, deleteSession, loadSessions } = useChatStore();
  const [hoveredSession, setHoveredSession] = React.useState<string | null>(null);

  React.useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/' || location.pathname === '/chat';
    return location.pathname.startsWith(path);
  };

  const handleNewSession = () => {
    createSession();
    if (location.pathname !== '/' && location.pathname !== '/chat') {
      navigate('/');
    }
  };

  const handleSwitchSession = (id: string) => {
    switchSession(id);
    if (location.pathname !== '/' && location.pathname !== '/chat') {
      navigate('/');
    }
  };

  const handleDeleteSession = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    deleteSession(id);
  };

  const formatTime = (ts: number) => {
    const d = new Date(ts);
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    if (isToday) return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
    return `${(d.getMonth() + 1).toString().padStart(2, '0')}/${d.getDate().toString().padStart(2, '0')}`;
  };

  return (
    <aside style={styles.sidebar}>
      <div style={styles.logoArea}>
        <FlaskConical size={24} color="#4F8CFF" />
        <span style={styles.logoText}>化工园区RAG</span>
      </div>
      <div style={styles.divider} />
      <nav style={styles.nav}>
        {navItems.map((item) => {
          const active = isActive(item.path);
          return (
            <div
              key={item.key}
              style={{ ...styles.navItem, ...(active ? styles.navItemActive : {}) }}
              onClick={() => navigate(item.path)}
              onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'rgba(79, 140, 255, 0.08)'; }}
              onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent'; }}
            >
              <span style={{ ...styles.navIcon, color: active ? '#FFFFFF' : '#5E6E88' }}>{item.icon}</span>
              <span style={{ ...styles.navLabel, color: active ? '#FFFFFF' : '#94A3B8', fontWeight: active ? 600 : 400 }}>
                {item.label}
              </span>
            </div>
          );
        })}
      </nav>

      {/* 会话列表 */}
      <div style={styles.divider} />
      <div style={styles.sessionHeader}>
        <span style={styles.sessionTitle}>历史会话</span>
        <div style={styles.newSessionBtn} onClick={handleNewSession} title="新建会话">
          <Plus size={14} />
        </div>
      </div>
      <div style={styles.sessionList}>
        {sessions.map((s) => {
          const active = s.id === sessionId;
          const hovered = s.id === hoveredSession;
          return (
            <div
              key={s.id}
              style={{ ...styles.sessionItem, ...(active ? styles.sessionItemActive : {}) }}
              onClick={() => handleSwitchSession(s.id)}
              onMouseEnter={() => setHoveredSession(s.id)}
              onMouseLeave={() => setHoveredSession(null)}
            >
              <MessageCircle size={14} style={{ color: active ? '#FFFFFF' : '#5E6E88', flexShrink: 0 }} />
              <div style={styles.sessionInfo}>
                <span style={{ ...styles.sessionName, color: active ? '#FFFFFF' : '#94A3B8' }}>{s.title}</span>
                <span style={styles.sessionTime}>{formatTime(s.lastActive)}</span>
              </div>
              {hovered && sessions.length > 1 && (
                <div style={styles.deleteBtn} onClick={(e) => handleDeleteSession(e, s.id)} title="删除会话">
                  <Trash2 size={12} />
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div style={{ flex: 1 }} />
      <div style={styles.divider} />
      <div style={styles.userArea}>
        <div style={styles.avatar} />
        <span style={styles.userName}>管理员</span>
      </div>
    </aside>
  );
};

const styles: Record<string, React.CSSProperties> = {
  sidebar: { width: 260, minWidth: 260, height: '100vh', display: 'flex', flexDirection: 'column', background: 'linear-gradient(180deg, #040914 0%, #080E1E 50%, #0A1226 100%)', borderRight: '1px solid #1A2748', overflow: 'hidden' },
  logoArea: { display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 12, padding: '20px' },
  logoText: { fontSize: 15, fontWeight: 700, color: '#E4EBF5', fontFamily: "'Inter', sans-serif", whiteSpace: 'nowrap' },
  divider: { width: '100%', height: 1, background: '#1A2748' },
  nav: { display: 'flex', flexDirection: 'column', gap: 4, padding: '12px 8px' },
  navItem: { display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 12, padding: '10px 12px', borderRadius: 8, cursor: 'pointer', transition: 'all 0.2s ease', background: 'transparent', userSelect: 'none' },
  navItemActive: { background: 'linear-gradient(-90deg, #3B6FE8 0%, #5B8CF6 100%)' },
  navIcon: { display: 'flex', alignItems: 'center', justifyContent: 'center' },
  navLabel: { fontSize: 13, fontFamily: "'Inter', sans-serif", whiteSpace: 'nowrap' },
  sessionHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px 8px' },
  sessionTitle: { fontSize: 11, color: '#5E6E88', fontFamily: "'Inter', sans-serif", textTransform: 'uppercase', letterSpacing: '0.05em' },
  newSessionBtn: { display: 'flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 24, borderRadius: 6, cursor: 'pointer', color: '#5E6E88', background: 'rgba(79, 140, 255, 0.08)', transition: 'all 0.2s' },
  sessionList: { display: 'flex', flexDirection: 'column', gap: 2, padding: '0 8px', overflowY: 'auto', maxHeight: 300, flex: '0 1 auto' },
  sessionItem: { display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 8, padding: '8px 10px', borderRadius: 6, cursor: 'pointer', transition: 'all 0.15s ease', background: 'transparent', userSelect: 'none' },
  sessionItemActive: { background: 'rgba(79, 140, 255, 0.15)' },
  sessionInfo: { display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 },
  sessionName: { fontSize: 12, fontFamily: "'Inter', sans-serif", whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  sessionTime: { fontSize: 10, color: '#5E6E88', fontFamily: "'Inter', sans-serif" },
  deleteBtn: { display: 'flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 20, borderRadius: 4, cursor: 'pointer', color: '#EF4444', background: 'rgba(239, 68, 68, 0.1)', flexShrink: 0 },
  userArea: { display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 12, padding: '12px 16px' },
  avatar: { width: 32, height: 32, borderRadius: 16, background: 'linear-gradient(-135deg, #4F8CFF 14.645%, #8B5CF6 85.355%)' },
  userName: { fontSize: 12, color: '#94A3B8', fontFamily: "'Inter', sans-serif" },
};

export default Sidebar;
