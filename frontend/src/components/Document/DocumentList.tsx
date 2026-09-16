import React, { useEffect, useState } from 'react';
import { Table, Tag, Button, Space, Modal, message, Select } from 'antd';
import { DeleteOutlined, DownloadOutlined, ReloadOutlined, FileTextOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useDocumentStore } from '../../stores/documentStore';
import { downloadDocument } from '../../api/document';
import { getScopes } from '../../api/scopes';
import type { DocumentInfo, ScopeInfo } from '../../types';

const SCOPE_NAME_MAP: Record<string, string> = {
  finance: '财务',
  hr: '人力资源',
  production_scheduling: '排单生产',
  safety: '安全环保',
  quality: '质量',
  general: '综合',
};

const SCOPE_COLOR_MAP: Record<string, string> = {
  finance: '#FBBF24',
  hr: '#8B5CF6',
  production_scheduling: '#34D399',
  safety: '#EF4444',
  quality: '#4F8CFF',
  general: '#94A3B8',
};

const DocumentList: React.FC = () => {
  const { documents, isLoading, fetchDocuments, removeDocument, reuploadFile } = useDocumentStore();
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const [reuploadDocId, setReuploadDocId] = useState<string | null>(null);
  const [scopes, setScopes] = useState<ScopeInfo[]>([]);
  const [scopeFilter, setScopeFilter] = useState<string | undefined>(undefined);

  useEffect(() => {
    getScopes().then(setScopes).catch(() => {});
  }, []);

  useEffect(() => {
    fetchDocuments(scopeFilter);
  }, [scopeFilter, fetchDocuments]);

  const handleDelete = async (docId: string) => {
    try { await removeDocument(docId); message.success('文档已删除'); } catch (error) { message.error('删除失败'); }
  };

  const handleReuploadClick = (docId: string) => {
    setReuploadDocId(docId);
    fileInputRef.current?.click();
  };

  const handleReuploadFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !reuploadDocId) return;
    try {
      await reuploadFile(reuploadDocId, file);
      message.success('文件重新上传成功，正在后台处理');
    } catch (error) {
      message.error('重新上传失败');
    }
    setReuploadDocId(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const showDeleteConfirm = (record: DocumentInfo) => {
    Modal.confirm({
      title: '确认删除',
      icon: <ExclamationCircleOutlined style={{ color: '#EF4444' }} />,
      content: `删除文档「${record.filename}」后将无法恢复，该文档对应的知识库切片内容也会一并删除，请确认是否删除？`,
      okText: '确认删除', okType: 'danger', cancelText: '取消',
      onOk: () => handleDelete(record.doc_id),
    });
  };

  const handleDownload = async (record: DocumentInfo) => {
    try { await downloadDocument(record.doc_id, record.filename); } catch (error) { message.error('下载失败'); }
  };

  const getStatusTag = (status: string) => {
    switch (status) {
      case 'processing': return <Tag style={{ background: 'rgba(79, 140, 255, 0.1)', border: '1px solid rgba(79, 140, 255, 0.2)', color: '#4F8CFF', borderRadius: 4 }}>处理中</Tag>;
      case 'ready': return <Tag style={{ background: 'rgba(52, 211, 153, 0.1)', border: '1px solid rgba(52, 211, 153, 0.2)', color: '#34D399', borderRadius: 4 }}>就绪</Tag>;
      case 'error': return <Tag style={{ background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.2)', color: '#EF4444', borderRadius: 4 }}>错误</Tag>;
      default: return <Tag>{status}</Tag>;
    }
  };

  const getFileTypeTag = (type: string) => {
    const colorMap: Record<string, { bg: string; border: string; text: string }> = {
      pdf: { bg: 'rgba(239, 68, 68, 0.1)', border: 'rgba(239, 68, 68, 0.2)', text: '#EF4444' },
      docx: { bg: 'rgba(79, 140, 255, 0.1)', border: 'rgba(79, 140, 255, 0.2)', text: '#4F8CFF' },
      xlsx: { bg: 'rgba(52, 211, 153, 0.1)', border: 'rgba(52, 211, 153, 0.2)', text: '#34D399' },
      csv: { bg: 'rgba(251, 191, 36, 0.1)', border: 'rgba(251, 191, 36, 0.2)', text: '#FBBF24' },
      json: { bg: 'rgba(139, 92, 246, 0.1)', border: 'rgba(139, 92, 246, 0.2)', text: '#8B5CF6' },
      txt: { bg: 'rgba(148, 163, 184, 0.1)', border: 'rgba(148, 163, 184, 0.2)', text: '#94A3B8' },
      md: { bg: 'rgba(148, 163, 184, 0.1)', border: 'rgba(148, 163, 184, 0.2)', text: '#94A3B8' },
    };
    const c = colorMap[type] || colorMap.txt;
    return <Tag style={{ background: c.bg, border: `1px solid ${c.border}`, color: c.text, borderRadius: 4 }}>{type.toUpperCase()}</Tag>;
  };

  const renderScopeTags = (scopeIds: string[] = []) => {
    if (!scopeIds.length) return <span style={{ color: '#5E6E88', fontSize: 12 }}>未分配</span>;
    return (
      <Space size={2} wrap>
        {scopeIds.map((sid) => {
          const color = SCOPE_COLOR_MAP[sid] || '#94A3B8';
          const name = SCOPE_NAME_MAP[sid] || sid;
          return (
            <Tag key={sid} style={{ background: `${color}15`, border: `1px solid ${color}30`, color, borderRadius: 4, fontSize: 11 }}>
              {name}
            </Tag>
          );
        })}
      </Space>
    );
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const columns: ColumnsType<DocumentInfo> = [
    { title: '文件名', dataIndex: 'filename', key: 'filename', render: (text) => <Space><FileTextOutlined style={{ color: '#5E6E88' }} /><span style={{ color: '#E4EBF5' }}>{text}</span></Space> },
    { title: '类型', dataIndex: 'file_type', key: 'file_type', width: 80, render: (type) => getFileTypeTag(type) },
    { title: '大小', dataIndex: 'file_size', key: 'file_size', width: 100, render: (size) => <span style={{ color: '#94A3B8' }}>{formatFileSize(size)}</span> },
    { title: '分组', dataIndex: 'scope_ids', key: 'scope_ids', width: 180, render: (ids) => renderScopeTags(ids) },
    { title: '分块数', dataIndex: 'chunk_count', key: 'chunk_count', width: 80, render: (count) => <span style={{ color: '#94A3B8' }}>{count}</span> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (status) => getStatusTag(status) },
    { title: '上传时间', dataIndex: 'upload_time', key: 'upload_time', width: 180, render: (time) => <span style={{ color: '#94A3B8', fontSize: 12 }}>{new Date(time).toLocaleString()}</span> },
    { title: '操作', key: 'action', width: 160, render: (_, record) => (
      <Space size={4}>
        <Button type="text" icon={<DownloadOutlined />} size="small" style={{ color: '#4F8CFF' }} onClick={() => handleDownload(record)} />
        <Button type="text" icon={<ReloadOutlined />} size="small" style={{ color: '#FBBF24' }} onClick={() => handleReuploadClick(record.doc_id)} title="重新上传" />
        <Button type="text" danger icon={<DeleteOutlined />} size="small" onClick={() => showDeleteConfirm(record)} />
      </Space>
    )},
  ];

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.headerTitle}>知识库文档</span>
        <Space>
          <Select
            placeholder="按分组筛选"
            allowClear
            style={{ width: 160 }}
            value={scopeFilter}
            onChange={(v) => setScopeFilter(v)}
            options={[
              { label: '全部', value: '' },
              ...scopes.map((s) => ({ label: s.name, value: s.scope_id })),
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={() => fetchDocuments(scopeFilter)} loading={isLoading} style={styles.refreshBtn}>刷新</Button>
        </Space>
      </div>
      <input ref={fileInputRef} type="file" style={{ display: 'none' }} onChange={handleReuploadFile} />
      <div style={styles.tableWrapper}>
        <Table columns={columns} dataSource={documents} rowKey="doc_id" loading={isLoading} pagination={{ pageSize: 10 }} size="small" scroll={{ y: '100%' }} />
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: { background: '#0D1525', border: '1px solid #1A2748', borderRadius: 12, padding: 20, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexShrink: 0 },
  headerTitle: { fontSize: 15, fontWeight: 600, color: '#E4EBF5', fontFamily: "'Inter', sans-serif" },
  refreshBtn: { background: 'transparent', border: '1px solid #1A2748', color: '#94A3B8', borderRadius: 8, fontFamily: "'Inter', sans-serif" },
  tableWrapper: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
};

export default DocumentList;
