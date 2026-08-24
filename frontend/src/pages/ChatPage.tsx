import React from 'react';
import ChatWindow from '../components/Chat/ChatWindow';

const ChatPage: React.FC = () => {
  return (
    <div style={{ flex: 1, display: 'flex', padding: 24, minHeight: 0 }}>
      <ChatWindow />
    </div>
  );
};

export default ChatPage;
