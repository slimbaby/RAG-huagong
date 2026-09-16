import React, { useEffect, useState } from 'react';
import { Upload, Button, message, Progress, Tag, Select } from 'antd';
import { FileOutlined, InboxOutlined } from '@ant-design/icons';
import { useDocumentStore } from '../../stores/documentStore';
import { useAuthStore } from '../../stores/authStore';
import { getScopes } from '../../api/scopes';
import type { ScopeInfo } from '../../types';

const FileUploader: React.FC = () => {
  const { uploadFile, uploadProgress, clearUploadProgress } = useDocumentStore();
  const userScopeIds = useAuthStore((s) => s.scope_ids);
  const isAdmin = useAuthStore((s) => s.scope_ids.includes('all'));

  const [scopes, setScopes] = useState<ScopeInfo[]>([]);
  const [selectedScopes, setSelectedScopes] = useState<string[]>([]);

  const allowedTypes = ['.pdf', '.docx', '.xlsx', '.xls', '.csv', '.json', '.txt', '.md'];

  useEffect(() => {
    getScopes().then((list) => {
      // 非 admin 只能看到自己可见的 scope
      if (isAdmin) {
        setScopes(list);
      } else {
        setScopes(list.filter((s) => userScopeIds.includes(s.scope_id)));
      }
    }).catch(() => {});
  }, [isAdmin, userScopeIds]);

  const handleUpload = async (file: File) => {
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!allowedTypes.includes(ext)) {
      message.error(`不支持的文件类型: ${ext}`);
      return false;
    }
    if (file.size > 50 * 1024 * 1024) {
      message.error('文件大小不能超过50MB');
      return false;
    }
    if (selectedScopes.length === 0) {
      message.warning('请至少选择一个所属分组');
      return false;
    }
    await uploadFile(file, selectedScopes);
    return false;
  };

  const getStatusTag = (status: string) => {
    switch (status) {
      case 'uploading': return <Tag style={{ background: 'rgba(79, 140, 255, 0.1)', border: '1px solid rgba(79, 140, 255, 0.2)', color: '#4F8CFF', borderRadius: 4 }}>上传中</Tag>;
      case 'processing': return <Tag style={{ background: 'rgba(79, 140, 255, 0.1)', border: '1px solid rgba(79, 140, 255, 0.2)', color: '#4F8CFF', borderRadius: 4 }}>处理中</Tag>;

      case 'success': return <Tag style={{ background: 'rgba(52, 211, 153, 0.1)', border: '1px solid rgba(52, 211, 153, 0.2)', color: '#34D399', borderRadius: 4 }}>成功</Tag>;
      case 'error': return <Tag style={{ background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.2)', color: '#EF4444', borderRadius: 4 }}>失败</Tag>;
      default: return null;
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.headerTitle}>上传文档</span>
        {uploadProgress.length > 0 && (
          <Button type="text" size="small" onClick={clearUploadProgress} style={{ color: '#5E6E88', fontSize: 12 }}>清空记录</Button>
        )}
      </div>
      <div style={styles.scopeRow}>
        <span style={styles.scopeLabel}>所属分组：</span>
        <Select
          mode="multiple"
          placeholder="请选择文档所属分组（可多选）"
          value={selectedScopes}
          onChange={setSelectedScopes}
          style={{ flex: 1, minWidth: 280 }}
          options={scopes.map((s) => ({ label: s.name, value: s.scope_id }))}
          maxTagCount={4}
          allowClear
        />
      </div>
      <Upload beforeUpload={handleUpload} showUploadList={false} multiple accept={allowedTypes.join(',')}>
        <div style={styles.dropZone}>
          <InboxOutlined style={{ fontSize: 32, color: '#4F8CFF', marginBottom: 8 }} />
          <div style={styles.dropText}>点击或拖拽文件到此区域上传</div>
          <div style={styles.dropHint}>支持 PDF, Word, Excel, CSV, JSON, TXT, MD (最大50MB)</div>
        </div>
      </Upload>
      {uploadProgress.length > 0 && (
        <div style={styles.progressList}>
          {uploadProgress.map((item, index) => (
            <div key={index} style={styles.progressItem}>
              <div style={styles.progressInfo}>
                <FileOutlined style={{ color: '#5E6E88', fontSize: 13 }} />
                <span style={styles.progressFilename}>{item.filename}</span>
                {getStatusTag(item.status)}
                {item.status === 'processing' && item.pagesInfo && (
                  <span style={{ color: '#4F8CFF', fontSize: 12, fontFamily: 'monospace' }}>{item.pagesInfo}</span>
                )}
              </div>
              <Progress percent={item.progress} size="small" strokeColor={{ '0%': '#3B6FE8', '100%': '#6366F1' }} trailColor="#1A2748" status={item.status === 'error' ? 'exception' : item.status === 'success' ? 'success' : 'active'} />
              {item.error && <div style={{ color: '#EF4444', fontSize: 12, marginTop: 4 }}>{item.error}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: { background: '#0D1525', border: '1px solid #1A2748', borderRadius: 12, padding: 20 },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 },
  headerTitle: { fontSize: 15, fontWeight: 600, color: '#E4EBF5', fontFamily: "'Inter', sans-serif" },
  scopeRow: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 },
  scopeLabel: { fontSize: 13, color: '#94A3B8', fontFamily: "'Inter', sans-serif", flexShrink: 0 },
  dropZone: { padding: '28px 20px', border: '1px dashed #2A4080', borderRadius: 8, background: 'linear-gradient(180deg, #0A1224 0%, #0F192D 100%)', textAlign: 'center' as const, cursor: 'pointer', transition: 'border-color 0.2s' },
  dropText: { fontSize: 14, color: '#94A3B8', fontFamily: "'Inter', sans-serif", marginBottom: 4 },
  dropHint: { fontSize: 12, color: '#5E6E88', fontFamily: "'Inter', sans-serif" },
  progressList: { marginTop: 16, display: 'flex', flexDirection: 'column' as const, gap: 12 },
  progressItem: { padding: '10px 12px', background: '#0A1224', borderRadius: 8, border: '1px solid #1A2748' },
  progressInfo: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 },
  progressFilename: { fontSize: 13, color: '#E4EBF5', fontFamily: "'Inter', sans-serif", flex: 1 },
};

export default FileUploader;
