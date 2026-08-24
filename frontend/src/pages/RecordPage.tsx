import React, { useEffect } from 'react';
import { Input, Table, Tag, Button, Tooltip, Drawer, Timeline, Spin, Empty, Descriptions, Collapse } from 'antd';
import { SearchOutlined, ReloadOutlined, EyeOutlined, CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { TraceInfo } from '../types';
import { useTraceStore } from '../stores/traceStore';
import { getTrace } from '../api/trace';

const renderSpanOutputs = (outputs: any) => {
  if (!outputs) return null;
  if (outputs.results && Array.isArray(outputs.results)) {
    return (
      <div style={{ fontSize: 12 }}>
        <div style={{ color: '#94A3B8', marginBottom: 6 }}>检索到 {outputs.results_count} 个切片</div>
        {outputs.results.map((r: any, i: number) => (
          <div key={i} style={{ background: '#0A1224', borderRadius: 6, padding: '6px 10px', marginBottom: 6, border: '1px solid #1A2748' }}>
            <div style={{ display: 'flex', gap: 12, marginBottom: 4, flexWrap: 'wrap' }}>
              <span style={{ color: '#4F8CFF' }}>#{r.rank}</span>
              <span style={{ color: '#94A3B8' }}>{r.source_file}</span>
              {r.page_number > 0 && <span style={{ color: '#5E6E88' }}>第{r.page_number}页</span>}
              <span style={{ color: '#5E6E88' }}>{r.content_type}</span>
            </div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 4 }}>
              <span style={{ color: '#34D399' }}>向量: {r.vector_score}</span>
              <span style={{ color: '#FBBF24' }}>BM25: {r.bm25_score}</span>
              <span style={{ color: '#E4EBF5' }}>混合: {r.hybrid_score}</span>
            </div>
            <div style={{ color: '#94A3B8', wordBreak: 'break-all' }}>{r.content_preview}</div>
          </div>
        ))}
      </div>
    );
  }
  if (outputs.prompt_preview) {
    return (
      <div style={{ fontSize: 12 }}>
        <div style={{ color: '#5E6E88', marginBottom: 4 }}>Prompt:</div>
        <pre style={{ background: '#0A1224', borderRadius: 6, padding: 8, color: '#94A3B8', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 300, overflow: 'auto', border: '1px solid #1A2748', margin: 0 }}>
          {outputs.prompt_preview}
        </pre>
        {outputs.response_preview && (
          <>
            <div style={{ color: '#5E6E88', marginTop: 8, marginBottom: 4 }}>回答预览:</div>
            <pre style={{ background: '#0A1224', borderRadius: 6, padding: 8, color: '#34D399', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 200, overflow: 'auto', border: '1px solid #1A2748', margin: 0 }}>
              {outputs.response_preview}
            </pre>
          </>
        )}
      </div>
    );
  }
  if (outputs.rewritten) {
    return (
      <div style={{ fontSize: 12 }}>
        {outputs.rewritten && <div style={{ color: '#94A3B8', marginBottom: 4 }}><span style={{ color: '#5E6E88' }}>优化后查询: </span>{outputs.rewritten}</div>}
        {outputs.query_type && <div style={{ color: '#94A3B8', marginBottom: 4 }}><span style={{ color: '#5E6E88' }}>查询类型: </span>{outputs.query_type}</div>}
        {outputs.keywords?.length > 0 && <div style={{ color: '#94A3B8', marginBottom: 4 }}><span style={{ color: '#5E6E88' }}>关键词: </span>{outputs.keywords.join(', ')}</div>}
        {outputs.expanded_queries?.length > 0 && <div style={{ color: '#94A3B8' }}><span style={{ color: '#5E6E88' }}>扩展查询: </span>{outputs.expanded_queries.join(', ')}</div>}
      </div>
    );
  }
  return (
    <pre style={{ background: '#0A1224', borderRadius: 6, padding: 8, color: '#94A3B8', whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 12, border: '1px solid #1A2748', margin: 0 }}>
      {JSON.stringify(outputs, null, 2)}
    </pre>
  );
};

const RecordPage: React.FC = () => {
  const { traces, isLoading, fetchTraces } = useTraceStore();
  const [searchText, setSearchText] = React.useState('');
  const [detailOpen, setDetailOpen] = React.useState(false);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [traceDetail, setTraceDetail] = React.useState<any>(null);

  const handleViewDetail = async (record: TraceInfo) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setTraceDetail(null);
    try {
      const detail = await getTrace(record.trace_id);
      setTraceDetail(detail);
    } catch {
      setTraceDetail({ error: '获取详情失败' });
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => { fetchTraces({ limit: 100 }); }, [fetchTraces]);

  const getStatusTag = (status: string) => {
    switch (status) {
      case 'success': case 'ok': return <Tag style={{ background: 'rgba(52, 211, 153, 0.1)', border: '1px solid rgba(52, 211, 153, 0.2)', color: '#34D399', borderRadius: 4 }}>成功</Tag>;
      case 'error': return <Tag style={{ background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.2)', color: '#EF4444', borderRadius: 4 }}>失败</Tag>;
      case 'pending': case 'running': return <Tag style={{ background: 'rgba(251, 191, 36, 0.1)', border: '1px solid rgba(251, 191, 36, 0.2)', color: '#FBBF24', borderRadius: 4 }}>处理中</Tag>;
      default: return <Tag>{status}</Tag>;
    }
  };

  const formatTime = (timestamp?: number) => {
    if (!timestamp) return '-';
    const date = new Date(timestamp * 1000);
    const pad = (n: number) => n.toString().padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  };

  const columns: ColumnsType<TraceInfo> = [
    { title: 'Trace ID', dataIndex: 'trace_id', key: 'trace_id', width: 100, render: (text) => <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#4F8CFF' }}>{text?.substring(0, 8) || '-'}</span> },
    { title: '时间', dataIndex: 'start_time', key: 'start_time', width: 160, render: (ts) => <span style={{ color: '#94A3B8', fontFamily: 'monospace', fontSize: 12 }}>{formatTime(ts)}</span> },
    { title: '用户查询', dataIndex: 'user_query', key: 'user_query', ellipsis: true, render: (text) => <span style={{ color: '#E4EBF5' }}>{text}</span> },
    { title: '优化查询', dataIndex: 'optimized_query', key: 'optimized_query', ellipsis: true, render: (text) => <span style={{ color: text ? '#94A3B8' : '#5E6E88' }}>{text || '-'}</span> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (status) => getStatusTag(status) },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', width: 100, render: (ms) => <span style={{ color: '#94A3B8', fontFamily: 'monospace', fontSize: 12 }}>{ms ? `${Math.round(ms)}ms` : '-'}</span> },
    { title: '引用数', dataIndex: 'sources_count', key: 'sources_count', width: 80, render: (count) => <span style={{ color: '#94A3B8' }}>{count ?? 0}</span> },
    { title: 'Token用量', key: 'tokens', width: 140, render: (_, record) => {
      const tokens = record.tokens_used || { prompt: 0, completion: 0 };
      return <span style={{ color: '#94A3B8', fontFamily: 'monospace', fontSize: 12 }}>{tokens.prompt + tokens.completion}<span style={{ color: '#5E6E88', marginLeft: 4 }}>(P:{tokens.prompt} / C:{tokens.completion})</span></span>;
    }},
    { title: '操作', key: 'action', width: 80, render: (_, record) => (
      <Tooltip title="查看详情"><Button type="text" icon={<EyeOutlined />} size="small" style={{ color: '#4F8CFF' }} onClick={() => handleViewDetail(record)} /></Tooltip>
    )},
  ];

  const filteredData = traces.filter((t) => !searchText || t.user_query?.includes(searchText) || t.trace_id?.includes(searchText) || (t.optimized_query && t.optimized_query.includes(searchText)));

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>调用记录</h1>
          <p style={styles.subtitle}>查看系统查询调用的追踪记录和性能数据</p>
        </div>
      </div>
      <div style={styles.toolbar}>
        <Input placeholder="搜索查询内容或Trace ID..." prefix={<SearchOutlined style={{ color: '#5E6E88' }} />} value={searchText} onChange={(e) => setSearchText(e.target.value)} style={styles.searchInput} allowClear />
        <Button icon={<ReloadOutlined />} onClick={() => fetchTraces({ limit: 100 })} loading={isLoading} style={styles.refreshBtn}>刷新</Button>
      </div>
      <div style={styles.tableWrap}>
        <Table columns={columns} dataSource={filteredData} rowKey="trace_id" loading={isLoading} pagination={{ pageSize: 10 }} size="small" />
      </div>
      <Drawer title="调用详情" open={detailOpen} onClose={() => setDetailOpen(false)} width={600} styles={{ body: { background: '#0A1224', padding: 16 } }}>
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
        ) : traceDetail?.error ? (
          <Empty description={traceDetail.error} />
        ) : traceDetail ? (
          <div>
            <Descriptions column={1} size="small" labelStyle={{ color: '#5E6E88', width: 100 }} contentStyle={{ color: '#E4EBF5' }} style={{ marginBottom: 20, background: '#0D1525', borderRadius: 8, padding: 12 }}>
              <Descriptions.Item label="Trace ID"><span style={{ fontFamily: 'monospace', color: '#4F8CFF' }}>{traceDetail.trace_id}</span></Descriptions.Item>
              <Descriptions.Item label="用户查询">{traceDetail.user_query}</Descriptions.Item>
              <Descriptions.Item label="优化查询">{traceDetail.optimized_query || '-'}</Descriptions.Item>
              <Descriptions.Item label="状态">{getStatusTag(traceDetail.status)}</Descriptions.Item>
              <Descriptions.Item label="总耗时">{traceDetail.duration_ms ? `${Math.round(traceDetail.duration_ms)}ms` : '-'}</Descriptions.Item>
              <Descriptions.Item label="引用数">{traceDetail.sources_count ?? 0}</Descriptions.Item>
              <Descriptions.Item label="回答" contentStyle={{ whiteSpace: 'pre-wrap' }}>{traceDetail.answer || '-'}</Descriptions.Item>
            </Descriptions>
            <div style={{ color: '#E4EBF5', fontSize: 14, fontWeight: 600, marginBottom: 12 }}>执行步骤</div>
            {traceDetail.spans?.length > 0 ? (
              <Timeline items={traceDetail.spans.map((span: any) => ({
                dot: span.status === 'ok' ? <CheckCircleOutlined style={{ color: '#34D399', fontSize: 14 }} /> : span.status === 'error' ? <CloseCircleOutlined style={{ color: '#EF4444', fontSize: 14 }} /> : <ClockCircleOutlined style={{ color: '#FBBF24', fontSize: 14 }} />,
                children: (
                  <div style={{ background: '#0D1525', borderRadius: 8, padding: '8px 12px', border: '1px solid #1A2748' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                      <span style={{ color: '#E4EBF5', fontWeight: 500 }}>{span.operation_name}</span>
                      <span style={{ color: '#5E6E88', fontFamily: 'monospace', fontSize: 12 }}>{span.duration_ms ? `${Math.round(span.duration_ms)}ms` : '-'}</span>
                    </div>
                    <div style={{ color: '#5E6E88', fontSize: 12 }}>{span.span_type}</div>
                    {span.inputs && Object.keys(span.inputs).length > 0 && (
                      <div style={{ marginTop: 6, fontSize: 12, color: '#94A3B8' }}>
                        <span style={{ color: '#5E6E88' }}>输入: </span>
                        {Object.entries(span.inputs).map(([k, v]) => `${k}=${typeof v === 'string' ? v.substring(0, 100) : JSON.stringify(v)}`).join(', ')}
                      </div>
                    )}
                    {span.outputs && Object.keys(span.outputs).length > 0 && (
                      <div style={{ marginTop: 6 }}>
                        <Collapse size="small" items={[{ key: '1', label: <span style={{ color: '#5E6E88', fontSize: 12 }}>输出详情</span>, children: renderSpanOutputs(span.outputs) }]} style={{ background: 'transparent', border: '1px solid #1A2748' }} />
                      </div>
                    )}
                    {span.error_message && <div style={{ marginTop: 6, fontSize: 12, color: '#EF4444' }}>错误: {span.error_message}</div>}
                  </div>
                ),
              }))} />
            ) : (
              <Empty description="暂无步骤数据" />
            )}
          </div>
        ) : null}
      </Drawer>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  page: { flex: 1, display: 'flex', flexDirection: 'column', padding: 24, minHeight: 0 },
  header: { marginBottom: 24 },
  title: { fontSize: 22, fontWeight: 700, color: '#E4EBF5', fontFamily: "'Inter', sans-serif", margin: 0, marginBottom: 4 },
  subtitle: { fontSize: 13, color: '#5E6E88', fontFamily: "'Inter', sans-serif", margin: 0 },
  toolbar: { display: 'flex', gap: 12, marginBottom: 20, alignItems: 'center' },
  searchInput: { flex: 1, maxWidth: 400, background: '#0A1224', border: '1px solid #2A4080', borderRadius: 8, color: '#E4EBF5', fontFamily: "'Inter', sans-serif" },
  refreshBtn: { background: '#0D1525', border: '1px solid #1A2748', color: '#94A3B8', borderRadius: 8, fontFamily: "'Inter', sans-serif" },
  tableWrap: { flex: 1, background: '#0D1525', border: '1px solid #1A2748', borderRadius: 12, overflow: 'hidden', padding: '0 1px' },
};

export default RecordPage;
