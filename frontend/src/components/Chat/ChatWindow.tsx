import React, { useRef, useEffect } from 'react';
import { RobotOutlined, CheckCircleOutlined, LoadingOutlined, CloseCircleOutlined } from '@ant-design/icons';
import { useChatStore } from '../../stores/chatStore';
import type { StepProgress } from '../../types';
import MessageBubble from './MessageBubble';
import ChatInput from './ChatInput';

const StepProgressView: React.FC<{ steps: StepProgress[] }> = ({ steps }) => (
  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 16 }}>
    <div style={{ width: 32, height: 32, borderRadius: 8, background: 'linear-gradient(-90deg, #3B6FE8 0%, #5B8CF6 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
      <RobotOutlined style={{ fontSize: 14, color: '#FFFFFF' }} />
    </div>
    <div style={{ padding: '12px 16px', borderRadius: '12px 12px 12px 0', background: 'linear-gradient(-112.264deg, #121D36 14.645%, #182540 85.355%)', border: '1px solid #1A2748', minWidth: 200 }}>
      {steps.map((step, i) => (
        <div key={step.node} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: i < steps.length - 1 ? 8 : 0 }}>
          <span style={{ fontSize: 14, lineHeight: 1 }}>
            {step.status === 'completed' ? (
              <CheckCircleOutlined style={{ color: '#34D399' }} />
            ) : step.status === 'error' ? (
              <CloseCircleOutlined style={{ color: '#EF4444' }} />
            ) : (
              <LoadingOutlined style={{ color: '#4F8CFF' }} />
            )}
          </span>
          <span style={{ color: step.status === 'completed' ? '#94A3B8' : '#E4EBF5', fontSize: 13, fontFamily: "'Inter', sans-serif" }}>
            {step.label}
          </span>
        </div>
      ))}
    </div>
  </div>
);

const TypingIndicator: React.FC = () => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
    <div style={{ width: 32, height: 32, borderRadius: 8, background: 'linear-gradient(-90deg, #3B6FE8 0%, #5B8CF6 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
      <RobotOutlined style={{ fontSize: 14, color: '#FFFFFF' }} />
    </div>
    <div style={{ padding: '12px 16px', borderRadius: '12px 12px 12px 0', background: 'linear-gradient(-112.264deg, #121D36 14.645%, #182540 85.355%)', border: '1px solid #1A2748', display: 'flex', gap: 4 }}>
      {[0, 1, 2].map((i) => (
        <div key={i} style={{ width: 6, height: 6, borderRadius: 3, background: '#4F8CFF', opacity: 0.4, animation: `typingDot 1.4s infinite`, animationDelay: `${i * 0.2}s` }} />
      ))}
    </div>
  </div>
);

const ChatWindow: React.FC = () => {
  const { messages, isLoading } = useChatStore();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const loadingMsg = isLoading ? messages.find((msg) => msg.isLoading) : null;
  const hasSteps = loadingMsg?.steps && loadingMsg.steps.length > 0;

  return (
    <div style={styles.container}>
      <style>{`
        @keyframes typingDot { 0%, 100% { opacity: 0.4; transform: scale(1); } 50% { opacity: 1; transform: scale(1.3); } }
        @media (max-width: 768px) { .chat-messages { padding: 16px !important; } .chat-input-area { padding: 12px 16px !important; } }
      `}</style>
      <div style={styles.header}>
        <div style={styles.headerLeft}>
          <div style={styles.botIcon}><RobotOutlined style={{ fontSize: 16, color: '#FFFFFF' }} /></div>
          <span style={styles.headerTitle}>化工园区知识库助手</span>
          <div style={styles.statusBadge}>
            <div style={styles.statusDot} />
            <span style={styles.statusText}>在线</span>
          </div>
        </div>
      </div>
      <div className="chat-messages" style={styles.messages}>
        {messages.length === 0 ? (
          <div style={styles.empty}>
            <RobotOutlined style={{ fontSize: 48, color: '#2A4080', marginBottom: 16 }} />
            <div style={styles.emptyText}>开始提问吧</div>
            <div style={styles.emptyHint}>输入您的问题，化工知识库助手将为您解答</div>
          </div>
        ) : (
          messages.filter((msg) => !msg.isLoading).map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))
        )}
        {loadingMsg && (hasSteps ? <StepProgressView steps={loadingMsg.steps!} /> : <TypingIndicator />)}
        <div ref={messagesEndRef} />
      </div>
      <div className="chat-input-area" style={styles.inputArea}>
        <ChatInput />
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: { flex: 1, display: 'flex', flexDirection: 'column', background: '#0D1525', border: '1px solid #1A2748', borderRadius: 12, overflow: 'hidden' },
  header: { padding: '12px 20px', borderBottom: '1px solid #1A2748', background: 'linear-gradient(-90deg, #0D1525 0%, #101A2E 100%)' },
  headerLeft: { display: 'flex', alignItems: 'center', gap: 12 },
  botIcon: { width: 32, height: 32, borderRadius: 8, background: 'linear-gradient(-90deg, #3B6FE8 0%, #5B8CF6 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  headerTitle: { fontSize: 15, fontWeight: 600, color: '#E4EBF5', fontFamily: "'Inter', sans-serif" },
  statusBadge: { display: 'flex', alignItems: 'center', gap: 6, padding: '2px 10px', borderRadius: 12, background: 'rgba(52, 211, 153, 0.1)', border: '1px solid rgba(52, 211, 153, 0.2)' },
  statusDot: { width: 6, height: 6, borderRadius: 3, background: '#34D399' },
  statusText: { fontSize: 11, color: '#34D399', fontFamily: "'Inter', sans-serif" },
  messages: { flex: 1, overflow: 'auto', padding: 24 },
  empty: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%' },
  emptyText: { fontSize: 16, color: '#94A3B8', fontFamily: "'Inter', sans-serif", marginBottom: 8 },
  emptyHint: { fontSize: 13, color: '#5E6E88', fontFamily: "'Inter', sans-serif" },
  inputArea: { padding: '16px 20px', borderTop: '1px solid #1A2748', background: '#0D1525' },
};

export default ChatWindow;
