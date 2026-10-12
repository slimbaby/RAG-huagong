import React from 'react';
import { Tag, Tooltip, Spin } from 'antd';
import { UserOutlined, RobotOutlined, FileTextOutlined } from '@ant-design/icons';
import type { ChatMessage, CitationInfo } from '../../types';

interface MessageBubbleProps { message: ChatMessage; }

const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  const isUser = message.role === 'user';

  const renderCitations = (citations: CitationInfo[]) => {
    if (!citations || citations.length === 0) return null;
    return (
      <div style={{ marginTop: 12, borderTop: '1px solid #1A2748', paddingTop: 8 }}>
        <span style={{ fontSize: 12, color: '#5E6E88', display: 'flex', alignItems: 'center', gap: 4, marginBottom: 6 }}>
          <FileTextOutlined /> 引用来源：
        </span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {citations.map((cite, index) => (
            <Tooltip key={index} title={cite.content_preview}>
              <Tag style={{ cursor: 'pointer', marginBottom: 4, background: 'rgba(79, 140, 255, 0.1)', border: '1px solid rgba(79, 140, 255, 0.2)', color: '#4F8CFF', borderRadius: 4 }}>
                {cite.source_file}
                {cite.page_number && ` 第${cite.page_number}页`}
                {cite.line_start && ` 第${cite.line_start}行`}
              </Tag>
            </Tooltip>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', marginBottom: 16 }}>
      {!isUser && (
        <div style={{ width: 32, height: 32, borderRadius: 8, background: 'linear-gradient(-90deg, #3B6FE8 0%, #5B8CF6 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginRight: 10, flexShrink: 0 }}>
          <RobotOutlined style={{ fontSize: 14, color: '#FFFFFF' }} />
        </div>
      )}
      <div style={{ maxWidth: '70%', minWidth: 80 }}>
        <div style={{
          padding: '12px 16px', borderRadius: isUser ? '12px 12px 0 12px' : '12px 12px 12px 0',
          background: isUser ? 'linear-gradient(-100.768deg, #1A3060 14.645%, #1E3A78 85.355%)' : 'linear-gradient(-112.264deg, #121D36 14.645%, #182540 85.355%)',
          border: isUser ? '1px solid rgba(79, 140, 255, 0.15)' : '1px solid #1A2748',
        }}>
          {message.isLoading && !message.content ? (
            <Spin size="small" />
          ) : (
            <>
              <div style={{ margin: 0, color: '#E4EBF5', whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.6, fontFamily: "'Inter', sans-serif" }}>
                {message.content}{message.isLoading && message.content && <span style={{ animation: 'blink 1s step-end infinite', color: '#4F8CFF' }}>▎</span>}
              </div>
              {!isUser && !message.isLoading && message.citations && renderCitations(message.citations)}
            </>
          )}
        </div>
        <div style={{ fontSize: 11, marginTop: 4, textAlign: isUser ? 'right' : 'left', color: '#5E6E88', fontFamily: "'Inter', sans-serif" }}>
          {new Date(message.timestamp).toLocaleTimeString()}
        </div>
      </div>
      {isUser && (
        <div style={{ width: 32, height: 32, borderRadius: 16, background: 'linear-gradient(-135deg, #4F8CFF 14.645%, #8B5CF6 85.355%)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginLeft: 10, flexShrink: 0 }}>
          <UserOutlined style={{ fontSize: 14, color: '#FFFFFF' }} />
        </div>
      )}
    </div>
  );
};

export default MessageBubble;
