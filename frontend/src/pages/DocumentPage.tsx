import React from 'react';
import FileUploader from '../components/Upload/FileUploader';
import DocumentList from '../components/Document/DocumentList';

const DocumentPage: React.FC = () => {
  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <h1 style={styles.title}>文档管理</h1>
        <p style={styles.subtitle}>管理知识库中的文档，上传新文档以扩展知识库</p>
      </div>
      <div style={styles.content}>
        <FileUploader />
        <DocumentList />
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  page: { flex: 1, display: 'flex', flexDirection: 'column', padding: 24, minHeight: 0 },
  header: { marginBottom: 24 },
  title: { fontSize: 22, fontWeight: 700, color: '#E4EBF5', fontFamily: "'Inter', sans-serif", margin: 0, marginBottom: 4 },
  subtitle: { fontSize: 13, color: '#5E6E88', fontFamily: "'Inter', sans-serif", margin: 0 },
  content: { display: 'flex', flexDirection: 'column', gap: 20, flex: 1, minHeight: 0, overflow: 'auto' },
};

export default DocumentPage;
