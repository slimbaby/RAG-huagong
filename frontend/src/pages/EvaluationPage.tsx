import React, { useEffect, useState, useCallback } from 'react';
import { Table, Tag, Button, Drawer, Descriptions, Tabs, Progress, Input, Space, Select, Switch, message, Spin, Empty, Statistic, Card, Row, Col } from 'antd';
import { ReloadOutlined, PlayCircleOutlined, SearchOutlined, CheckCircleOutlined, CloseCircleOutlined, ExperimentOutlined, DatabaseOutlined, BarChartOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { EvalReportSummary, EvalReportDetail, EvalCase, EvalDatasetStats } from '../types';
import { useEvalStore } from '../stores/evalStore';

const DIMENSION_LABELS: Record<string, string> = {
  rule_score: '规则检查',
  ragas_faithfulness: 'RAGAS 忠实度',
  ragas_context_precision: 'RAGAS 上下文精度',
  ragas_context_recall: 'RAGAS 上下文召回',
  hallucination_score: '反幻觉',
  citation_score: '引用准确性',
  consistency_score: '多轮一致性',
  judge_score: 'LLM Judge',
};

const getScoreColor = (score: number) => {
  if (score >= 8) return '#34D399';
  if (score >= 6) return '#FBBF24';
  return '#EF4444';
};

const formatTime = (timestamp: string) => {
  if (!timestamp) return '-';
  const d = new Date(timestamp);
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

// ── 报告详情 Drawer ──

const ReportDetailDrawer: React.FC<{
  open: boolean;
  onClose: () => void;
  report: EvalReportDetail | null;
  loading: boolean;
}> = ({ open, onClose, report, loading }) => {
  const caseColumns: ColumnsType<any> = [
    { title: '用例 ID', dataIndex: 'case_id', key: 'case_id', width: 140, render: (t) => <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#4F8CFF' }}>{t}</span> },
    { title: '问题', dataIndex: 'question', key: 'question', ellipsis: true, render: (t) => <span style={{ color: '#E4EBF5' }}>{t}</span> },
    { title: '类别', dataIndex: 'category', key: 'category', width: 100, render: (t) => <Tag style={{ background: 'rgba(79,140,255,0.1)', border: '1px solid rgba(79,140,255,0.2)', color: '#4F8CFF', borderRadius: 4 }}>{t}</Tag> },
    { title: '难度', dataIndex: 'difficulty', key: 'difficulty', width: 70, render: (t) => {
      const colors: Record<string, string> = { easy: '#34D399', medium: '#FBBF24', hard: '#EF4444' };
      return <Tag style={{ background: `${colors[t]}15`, border: `1px solid ${colors[t]}30`, color: colors[t] || '#94A3B8', borderRadius: 4 }}>{t}</Tag>;
    }},
    { title: '总分', dataIndex: 'overall_score', key: 'overall_score', width: 80, render: (v) => <span style={{ color: getScoreColor(v), fontWeight: 600 }}>{v?.toFixed(1)}</span> },
    { title: '幻觉', key: 'hallucination', width: 70, render: (_, r) => <span style={{ color: getScoreColor(r.scores?.hallucination) }}>{r.scores?.hallucination?.toFixed(1)}</span> },
    { title: '引用', key: 'citation', width: 70, render: (_, r) => <span style={{ color: getScoreColor(r.scores?.citation) }}>{r.scores?.citation?.toFixed(1)}</span> },
    { title: '状态', key: 'status', width: 70, render: (_, r) => r.passed
      ? <Tag style={{ background: 'rgba(52,211,153,0.1)', border: '1px solid rgba(52,211,153,0.2)', color: '#34D399', borderRadius: 4 }}>通过</Tag>
      : <Tag style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.2)', color: '#EF4444', borderRadius: 4 }}>失败</Tag>
    },
  ];

  return (
    <Drawer title="评测报告详情" open={open} onClose={onClose} width={800} styles={{ body: { background: '#0A1224', padding: 16 } }}>
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
      ) : !report ? (
        <Empty description="暂无数据" />
      ) : (
        <div>
          {/* 基本信息 */}
          <Descriptions column={2} size="small" labelStyle={{ color: '#5E6E88' }} contentStyle={{ color: '#E4EBF5' }} style={{ background: '#0D1525', borderRadius: 8, padding: 12, marginBottom: 16 }}>
            <Descriptions.Item label="评测 ID"><span style={{ fontFamily: 'monospace', color: '#4F8CFF' }}>{report.eval_id}</span></Descriptions.Item>
            <Descriptions.Item label="时间">{formatTime(report.timestamp)}</Descriptions.Item>
            <Descriptions.Item label="总用例数">{report.total_cases}</Descriptions.Item>
            <Descriptions.Item label="通过数"><span style={{ color: '#34D399' }}>{report.passed_cases}</span></Descriptions.Item>
            <Descriptions.Item label="失败数"><span style={{ color: '#EF4444' }}>{report.failed_cases}</span></Descriptions.Item>
            <Descriptions.Item label="平均分"><span style={{ color: getScoreColor(report.avg_score), fontWeight: 700, fontSize: 16 }}>{report.avg_score?.toFixed(2)}</span></Descriptions.Item>
            <Descriptions.Item label="门禁">{report.gate_passed
              ? <Tag icon={<CheckCircleOutlined />} color="success">通过</Tag>
              : <Tag icon={<CloseCircleOutlined />} color="error">未通过</Tag>
            }</Descriptions.Item>
            {report.gate_reasons?.length > 0 && (
              <Descriptions.Item label="门禁原因">{report.gate_reasons.join('; ')}</Descriptions.Item>
            )}
          </Descriptions>

          {/* 维度分数 */}
          <div style={{ color: '#E4EBF5', fontSize: 14, fontWeight: 600, marginBottom: 12 }}>维度分数</div>
          <Row gutter={[12, 12]} style={{ marginBottom: 20 }}>
            {Object.entries(report.dimension_scores || {}).map(([key, score]) => (
              <Col span={8} key={key}>
                <div style={{ background: '#0D1525', borderRadius: 8, padding: '10px 12px', border: '1px solid #1A2748' }}>
                  <div style={{ color: '#5E6E88', fontSize: 11, marginBottom: 6 }}>{DIMENSION_LABELS[key] || key}</div>
                  <Progress
                    percent={score * 10}
                    size="small"
                    strokeColor={getScoreColor(score)}
                    trailColor="#1A2748"
                    format={() => <span style={{ color: getScoreColor(score), fontWeight: 600, fontSize: 12 }}>{score?.toFixed(1)}</span>}
                  />
                </div>
              </Col>
            ))}
          </Row>

          {/* 用例结果 */}
          <div style={{ color: '#E4EBF5', fontSize: 14, fontWeight: 600, marginBottom: 12 }}>用例明细</div>
          <Table columns={caseColumns} dataSource={report.case_results} rowKey="case_id" size="small" pagination={false} />
        </div>
      )}
    </Drawer>
  );
};

// ── 报告列表 Tab ──

const ReportsTab: React.FC = () => {
  const { reports, isLoading, fetchReports } = useEvalStore();
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [currentReport, setCurrentReport] = useState<EvalReportDetail | null>(null);

  useEffect(() => { fetchReports(); }, [fetchReports]);

  const handleViewDetail = async (record: EvalReportSummary) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setCurrentReport(null);
    try {
      const { getEvalReport } = await import('../api/evaluation');
      const detail = await getEvalReport(record.eval_id);
      setCurrentReport(detail);
    } catch {
      message.error('获取报告详情失败');
    } finally {
      setDetailLoading(false);
    }
  };

  const columns: ColumnsType<EvalReportSummary> = [
    { title: '评测 ID', dataIndex: 'eval_id', key: 'eval_id', width: 200, render: (t) => <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#4F8CFF' }}>{t}</span> },
    { title: '时间', dataIndex: 'timestamp', key: 'timestamp', width: 160, render: (t) => <span style={{ color: '#94A3B8', fontFamily: 'monospace', fontSize: 12 }}>{formatTime(t)}</span> },
    { title: '平均分', dataIndex: 'avg_score', key: 'avg_score', width: 100, sorter: (a, b) => a.avg_score - b.avg_score, render: (v) => <span style={{ color: getScoreColor(v), fontWeight: 700, fontSize: 15 }}>{v?.toFixed(2)}</span> },
    { title: '通过率', key: 'pass_rate', width: 120, render: (_, r) => {
      const rate = r.total_cases > 0 ? (r.passed_cases / r.total_cases) * 100 : 0;
      return <Progress percent={Math.round(rate)} size="small" strokeColor={rate >= 80 ? '#34D399' : rate >= 50 ? '#FBBF24' : '#EF4444'} trailColor="#1A2748" />;
    }},
    { title: '用例数', dataIndex: 'total_cases', key: 'total_cases', width: 80, render: (v) => <span style={{ color: '#94A3B8' }}>{v}</span> },
    { title: '通过/失败', key: 'pass_fail', width: 100, render: (_, r) => <span><span style={{ color: '#34D399' }}>{r.passed_cases}</span><span style={{ color: '#5E6E88' }}> / </span><span style={{ color: '#EF4444' }}>{r.total_cases - r.passed_cases}</span></span> },
    { title: '门禁', dataIndex: 'gate_passed', key: 'gate_passed', width: 80, render: (v) => v
      ? <Tag style={{ background: 'rgba(52,211,153,0.1)', border: '1px solid rgba(52,211,153,0.2)', color: '#34D399', borderRadius: 4 }}>通过</Tag>
      : <Tag style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.2)', color: '#EF4444', borderRadius: 4 }}>未通过</Tag>
    },
    { title: '操作', key: 'action', width: 80, render: (_, record) => (
      <Button type="text" size="small" style={{ color: '#4F8CFF' }} onClick={() => handleViewDetail(record)}>详情</Button>
    )},
  ];

  return (
    <>
      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        <Button icon={<ReloadOutlined />} onClick={fetchReports} loading={isLoading} style={btnStyle}>刷新</Button>
      </div>
      <div style={tableWrapStyle}>
        <Table columns={columns} dataSource={reports} rowKey="eval_id" loading={isLoading} pagination={{ pageSize: 10 }} size="small" />
      </div>
      <ReportDetailDrawer open={detailOpen} onClose={() => setDetailOpen(false)} report={currentReport} loading={detailLoading} />
    </>
  );
};

// ── 评测集 Tab ──

const DatasetTab: React.FC = () => {
  const { dataset, datasetStats, isLoading, fetchDataset, fetchDatasetStats } = useEvalStore();
  const [searchText, setSearchText] = useState('');

  useEffect(() => { fetchDataset(); fetchDatasetStats(); }, [fetchDataset, fetchDatasetStats]);

  const columns: ColumnsType<EvalCase> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 160, render: (t) => <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#4F8CFF' }}>{t}</span> },
    { title: '问题', dataIndex: 'question', key: 'question', ellipsis: true, render: (t) => <span style={{ color: '#E4EBF5' }}>{t}</span> },
    { title: '类别', dataIndex: 'category', key: 'category', width: 100, render: (t) => <Tag style={{ background: 'rgba(79,140,255,0.1)', border: '1px solid rgba(79,140,255,0.2)', color: '#4F8CFF', borderRadius: 4 }}>{t}</Tag> },
    { title: '难度', dataIndex: 'difficulty', key: 'difficulty', width: 70, render: (t) => {
      const colors: Record<string, string> = { easy: '#34D399', medium: '#FBBF24', hard: '#EF4444' };
      return <Tag style={{ background: `${colors[t]}15`, border: `1px solid ${colors[t]}30`, color: colors[t] || '#94A3B8', borderRadius: 4 }}>{t}</Tag>;
    }},
    { title: '层级', dataIndex: 'layer', key: 'layer', width: 80, render: (t) => {
      const colors: Record<string, string> = { core: '#4F8CFF', document: '#34D399', regression: '#FBBF24' };
      return <Tag style={{ background: `${colors[t] || '#5E6E88'}15`, border: `1px solid ${colors[t] || '#5E6E88'}30`, color: colors[t] || '#94A3B8', borderRadius: 4 }}>{t}</Tag>;
    }},
    { title: '来源文档', dataIndex: 'expected_source', key: 'expected_source', width: 140, ellipsis: true, render: (t) => <span style={{ color: '#94A3B8', fontSize: 12 }}>{t || '-'}</span> },
    { title: '状态', key: 'status', width: 70, render: (_, r) => r.stale
      ? <Tag style={{ background: 'rgba(100,116,139,0.1)', border: '1px solid rgba(100,116,139,0.2)', color: '#64748B', borderRadius: 4 }}>失效</Tag>
      : <Tag style={{ background: 'rgba(52,211,153,0.1)', border: '1px solid rgba(52,211,153,0.2)', color: '#34D399', borderRadius: 4 }}>有效</Tag>
    },
  ];

  const filtered = dataset.filter((c) =>
    !searchText || c.question.includes(searchText) || c.id.includes(searchText) || c.category.includes(searchText)
  );

  return (
    <>
      {/* 统计卡片 */}
      {datasetStats && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={6}><div style={cardStyle}><Statistic title={<span style={{ color: '#5E6E88' }}>总用例数</span>} value={datasetStats.total} valueStyle={{ color: '#E4EBF5', fontSize: 24 }} /></div></Col>
          <Col span={6}><div style={cardStyle}><Statistic title={<span style={{ color: '#5E6E88' }}>有效用例</span>} value={datasetStats.active} valueStyle={{ color: '#34D399', fontSize: 24 }} /></div></Col>
          <Col span={6}><div style={cardStyle}><Statistic title={<span style={{ color: '#5E6E88' }}>失效用例</span>} value={datasetStats.stale} valueStyle={{ color: '#64748B', fontSize: 24 }} /></div></Col>
          <Col span={6}><div style={cardStyle}>
            <div style={{ color: '#5E6E88', fontSize: 14, marginBottom: 4 }}>层级分布</div>
            {Object.entries(datasetStats.by_layer || {}).map(([layer, count]) => (
              <div key={layer} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                <span style={{ color: '#94A3B8', fontSize: 12 }}>{layer}</span>
                <span style={{ color: '#E4EBF5', fontSize: 12, fontWeight: 600 }}>{count}</span>
              </div>
            ))}
          </div></Col>
        </Row>
      )}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        <Input placeholder="搜索问题或用例 ID..." prefix={<SearchOutlined style={{ color: '#5E6E88' }} />} value={searchText} onChange={(e) => setSearchText(e.target.value)} style={searchStyle} allowClear />
        <Button icon={<ReloadOutlined />} onClick={fetchDataset} loading={isLoading} style={btnStyle}>刷新</Button>
      </div>
      <div style={tableWrapStyle}>
        <Table columns={columns} dataSource={filtered} rowKey="id" loading={isLoading} pagination={{ pageSize: 15 }} size="small" />
      </div>
    </>
  );
};

// ── 运行评测 Tab ──

const RunTab: React.FC = () => {
  const { runEval, pollTaskStatus, fetchReports } = useEvalStore();
  const [layer, setLayer] = useState<string>('all');
  const [sampleRatio, setSampleRatio] = useState<number>(0.2);
  const [gateEnabled, setGateEnabled] = useState(true);
  const [includeStale, setIncludeStale] = useState(false);
  const [running, setRunning] = useState(false);
  const [taskResult, setTaskResult] = useState<any>(null);

  const handleRun = async () => {
    setRunning(true);
    setTaskResult(null);
    try {
      const taskId = await runEval({
        layer: layer === 'all' ? undefined : layer,
        gate_enabled: gateEnabled,
        include_stale: includeStale,
        sample_ratio: layer === 'core' ? undefined : sampleRatio,
      });
      message.success(`评测已启动: ${taskId}`);

      // 轮询任务状态
      const poll = async () => {
        for (let i = 0; i < 120; i++) {
          await new Promise((r) => setTimeout(r, 2000));
          try {
            const status = await pollTaskStatus(taskId);
            if (status.status === 'completed' || status.status === 'failed') {
              setTaskResult(status);
              setRunning(false);
              if (status.status === 'completed') {
                message.success('评测完成');
                fetchReports();
              } else {
                message.error(`评测失败: ${status.error}`);
              }
              return;
            }
          } catch { /* retry */ }
        }
        setRunning(false);
        message.warning('轮询超时，请稍后查看报告列表');
      };
      poll();
    } catch (e: any) {
      setRunning(false);
      message.error(e.message || '启动评测失败');
    }
  };

  return (
    <div style={{ maxWidth: 600 }}>
      <div style={{ background: '#0D1525', borderRadius: 12, padding: 24, border: '1px solid #1A2748', marginBottom: 20 }}>
        <div style={{ color: '#E4EBF5', fontSize: 15, fontWeight: 600, marginBottom: 16 }}>评测参数</div>
        <div style={{ marginBottom: 16 }}>
          <div style={{ color: '#5E6E88', fontSize: 13, marginBottom: 6 }}>评测层级</div>
          <Select value={layer} onChange={setLayer} style={{ width: '100%' }} options={[
            { value: 'all', label: '全部' },
            { value: 'core', label: '核心用例 (core)' },
            { value: 'document', label: '文档用例 (document)' },
            { value: 'regression', label: '回归用例 (regression)' },
          ]} />
        </div>
        <div style={{ marginBottom: 16 }}>
          <div style={{ color: '#5E6E88', fontSize: 13, marginBottom: 6 }}>
            抽样比例 <span style={{ fontSize: 12 }}>(core 层始终全量，其他层按比例随机抽样)</span>
          </div>
          <Select value={sampleRatio} onChange={setSampleRatio} style={{ width: '100%' }} options={[
            { value: 0.1, label: '10% (快速)' },
            { value: 0.2, label: '20% (推荐)' },
            { value: 0.5, label: '50%' },
            { value: 1, label: '100% 全量 (最慢)' },
          ]} />
        </div>
        <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
          <Switch checked={gateEnabled} onChange={setGateEnabled} />
          <span style={{ color: '#94A3B8', fontSize: 13 }}>启用门禁检查</span>
        </div>
        <div style={{ marginBottom: 20, display: 'flex', alignItems: 'center', gap: 12 }}>
          <Switch checked={includeStale} onChange={setIncludeStale} />
          <span style={{ color: '#94A3B8', fontSize: 13 }}>包含失效用例</span>
        </div>
        <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleRun} loading={running} size="large" block>
          {running ? '评测运行中...' : '开始评测'}
        </Button>
      </div>

      {taskResult && (
        <div style={{ background: '#0D1525', borderRadius: 12, padding: 24, border: `1px solid ${taskResult.status === 'completed' ? '#34D39930' : '#EF444430'}` }}>
          <div style={{ color: '#E4EBF5', fontSize: 15, fontWeight: 600, marginBottom: 12 }}>评测结果</div>
          <Descriptions column={1} size="small" labelStyle={{ color: '#5E6E88' }} contentStyle={{ color: '#E4EBF5' }}>
            <Descriptions.Item label="状态">{taskResult.status === 'completed'
              ? <Tag color="success">完成</Tag>
              : <Tag color="error">失败</Tag>
            }</Descriptions.Item>
            {taskResult.eval_id && <Descriptions.Item label="报告 ID"><span style={{ fontFamily: 'monospace', color: '#4F8CFF' }}>{taskResult.eval_id}</span></Descriptions.Item>}
            {taskResult.avg_score && <Descriptions.Item label="平均分"><span style={{ color: getScoreColor(parseFloat(taskResult.avg_score)), fontWeight: 700, fontSize: 18 }}>{parseFloat(taskResult.avg_score).toFixed(2)}</span></Descriptions.Item>}
            {taskResult.gate_passed !== undefined && <Descriptions.Item label="门禁">{taskResult.gate_passed === 'True' || taskResult.gate_passed === true
              ? <Tag icon={<CheckCircleOutlined />} color="success">通过</Tag>
              : <Tag icon={<CloseCircleOutlined />} color="error">未通过</Tag>
            }</Descriptions.Item>}
            {taskResult.error && <Descriptions.Item label="错误"><span style={{ color: '#EF4444' }}>{taskResult.error}</span></Descriptions.Item>}
          </Descriptions>
        </div>
      )}
    </div>
  );
};

// ── 主页面 ──

const btnStyle: React.CSSProperties = { background: '#0D1525', border: '1px solid #1A2748', color: '#94A3B8', borderRadius: 8 };
const searchStyle: React.CSSProperties = { flex: 1, maxWidth: 400, background: '#0A1224', border: '1px solid #2A4080', borderRadius: 8, color: '#E4EBF5' };
const tableWrapStyle: React.CSSProperties = { flex: 1, background: '#0D1525', border: '1px solid #1A2748', borderRadius: 12, overflow: 'hidden', padding: '0 1px' };
const cardStyle: React.CSSProperties = { background: '#0D1525', borderRadius: 12, padding: 16, border: '1px solid #1A2748' };

const EvaluationPage: React.FC = () => {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: 24, minHeight: 0 }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: '#E4EBF5', fontFamily: "'Inter', sans-serif", margin: 0, marginBottom: 4 }}>质量评测</h1>
        <p style={{ fontSize: 13, color: '#5E6E88', fontFamily: "'Inter', sans-serif", margin: 0 }}>RAG 系统检索质量评估与报告管理</p>
      </div>
      <Tabs
        defaultActiveKey="reports"
        items={[
          { key: 'reports', label: <span><BarChartOutlined /> 评测报告</span>, children: <ReportsTab /> },
          { key: 'dataset', label: <span><DatabaseOutlined /> 评测集</span>, children: <DatasetTab /> },
          { key: 'run', label: <span><ExperimentOutlined /> 运行评测</span>, children: <RunTab /> },
        ]}
      />
    </div>
  );
};

export default EvaluationPage;
