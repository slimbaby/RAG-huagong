import React, { useState } from 'react';
import { Input, Button } from 'antd';
import { SendOutlined, ClearOutlined } from '@ant-design/icons';
import { useChatStore } from '../../stores/chatStore';

const { TextArea } = Input;

const ChatInput: React.FC = () => {
  const [question, setQuestion] = useState('');
  const { sendMessage, isLoading, clearMessages } = useChatStore();

  const handleSend = async () => {
    const trimmed = question.trim();
    if (!trimmed || isLoading) return;
    setQuestion('');
    await sendMessage(trimmed);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div style={styles.wrapper}>
      <div style={styles.inputRow}>
        <div style={styles.inputContainer}>
          <TextArea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入您的问题... (Enter发送，Shift+Enter换行)"
            autoSize={{ minRows: 1, maxRows: 4 }}
            disabled={isLoading}
            style={styles.textarea}
            bordered={false}
          />
        </div>
        <div style={styles.actions}>
          <Button icon={<ClearOutlined />} onClick={clearMessages} title="清空对话" style={styles.clearBtn} />
          <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={isLoading} disabled={!question.trim()} style={styles.sendBtn}>
            发送
          </Button>
        </div>
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  wrapper: { width: '100%' },
  inputRow: { display: 'flex', alignItems: 'flex-end', gap: 12 },
  inputContainer: { flex: 1, background: 'linear-gradient(180deg, #0A1224 0%, #0F192D 100%)', border: '1px solid #2A4080', borderRadius: 12, padding: '2px 0', transition: 'border-color 0.2s' },
  textarea: { background: 'transparent', color: '#E4EBF5', fontSize: 13, fontFamily: "'Inter', sans-serif", resize: 'none' as const, padding: '10px 14px' },
  actions: { display: 'flex', gap: 8, flexShrink: 0 },
  clearBtn: { width: 40, height: 40, borderRadius: 8, background: '#0D1525', border: '1px solid #1A2748', color: '#5E6E88', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  sendBtn: { height: 40, borderRadius: 8, background: 'linear-gradient(-90deg, #3B6FE8 0%, #6366F1 100%)', border: 'none', color: '#FFFFFF', fontWeight: 600, fontFamily: "'Inter', sans-serif", display: 'flex', alignItems: 'center', gap: 6 },
};

export default ChatInput;
