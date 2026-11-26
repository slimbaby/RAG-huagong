import React, { useCallback, useEffect, useState } from 'react';
import { Button, Card, Drawer, Modal, Popconfirm, Segmented, Space, Table, Tag, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate, useParams } from 'react-router-dom';
import type { FlowApplication, FlowRenderConfig } from '../api/flow';
import {
  SOURCE_LABELS, STATUS_COLORS, cancelFlowApplication, getFlowPass,
  listFlowApplications, reopenFlowApplication, submitFlowApplication,
} from '../api/flow';
import { useFlowStore } from '../stores/flowStore';
import FlowApplicationDetail from '../components/Flow/FlowApplicationDetail';

const FILTER_ALL = [
  { label: '全部', value: '' },
  { label: '草稿', value: 'draft' },
  { label: '待审核', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已驳回', value: 'rejected' },
  { label: '已撤回', value: 'cancelled' },
];

/** 通用「我的申请」：列、状态名、操作按后端配置与固定 5 态骨架派生。 */
const FlowMyPage: React.FC = () => {
  const { flowId = '' } = useParams<{ flowId: string }>();
  const navigate = useNavigate();
  const configs = useFlowStore((s) => s.configs);
  const loadConfig = useFlowStore((s) => s.loadConfig);
  const config: FlowRenderConfig | undefined = configs[flowId];
  const [rows, setRows] = useState<FlowApplication[]>([]);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<FlowApplication | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [passInfo, setPassInfo] = useState<any | null>(null);

  useEffect(() => { loadConfig(flowId); }, [flowId, loadConfig]);

  const load = useCallback(async () => {
    if (!flowId) return;
    setLoading(true);
    try {
      const res = await listFlowApplications(flowId, status || undefined, 'mine');
      setRows(res.applications || []);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载申请列表失败');
    } finally { setLoading(false); }
  }, [flowId, status]);

  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<any>, ok: string) => {
    try { await fn(); message.success(ok); load(); }
    catch (e: any) { message.error(e?.response?.data?.detail || '操作失败'); }
  };

  const showPass = async (id: string) => {
    try { setPassInfo(await getFlowPass(flowId, id)); }
    catch (e: any) { message.error(e?.response?.data?.detail || '凭证获取失败'); }
  };

  const labels = config?.state_labels || {};
  const filters = FILTER_ALL.map((f) => ({ ...f, label: f.value ? (labels[f.value] || f.label) : f.label }));
  const previewFields = (config?.fields || []).slice(0, 3);

  const columns: ColumnsType<FlowApplication> = [
    { title: '申请单号', dataIndex: 'id', width: 175, render: (v: string) => <span style={{ fontFamily: 'monospace' }}>{v}</span> },
    {
      title: '来源', dataIndex: 'source', width: 120,
      render: (_: string, r) => <Tag color={r.assisted_by_ai ? 'blue' : 'default'}>
        {r.assisted_by_ai ? SOURCE_LABELS.assisted : SOURCE_LABELS.manual}
      </Tag>,
    },
    { title: '标题', dataIndex: 'title', ellipsis: true, width: 160 },
    ...previewFields.map((f) => ({
      title: f.label, key: f.key, width: 130, ellipsis: true,
      render: (_: any, r: FlowApplication) => String(r.form_data?.[f.key] ?? '—'),
    })),
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: (v: string) => v?.slice(0, 19).replace('T', ' ') || '—' },
    {
      title: '状态', dataIndex: 'status', width: 120,
      render: (v: string) => <Tag color={STATUS_COLORS[v]}>{labels[v] || v}</Tag>,
    },
    {
      title: '操作', key: 'action', width: 260, fixed: 'right' as const,
      render: (_: any, r: FlowApplication) => (
        <Space size={4} wrap>
          <Button type="link" size="small" onClick={() => { setDetail(r); setDrawerOpen(true); }}>详情</Button>
          {r.status === 'draft' && (
            <>
              <Button type="link" size="small" onClick={() => navigate(`/flows/${flowId}/apply?id=${r.id}`)}>核对 / 编辑</Button>
              <Popconfirm title="确认提交后将进入管理员待审核列表" onConfirm={() => act(() => submitFlowApplication(flowId, r.id), '已确认提交')}>
                <Button type="link" size="small">确认提交</Button>
              </Popconfirm>
            </>
          )}
          {(r.status === 'draft' || r.status === 'pending') && (
            <Popconfirm title="确定撤回该申请吗？" onConfirm={() => act(() => cancelFlowApplication(flowId, r.id), '申请已撤回')}>
              <Button type="link" size="small" danger>撤回</Button>
            </Popconfirm>
          )}
          {r.status === 'rejected' && (
            <Popconfirm title="把原单退回草稿，修改后可再次提交（保留单号与审核记录）"
                        onConfirm={() => act(() => reopenFlowApplication(flowId, r.id), '已回到草稿').then(() => navigate(`/flows/${flowId}/apply?id=${r.id}`))}>
              <Button type="link" size="small">改原单重提</Button>
            </Popconfirm>
          )}
          {r.status === 'approved' && (
            <Button type="link" size="small" onClick={() => showPass(r.id)}>查看结果</Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <div>
          <h2 style={styles.title}>我的{config?.name || '申请'}</h2>
          <div style={styles.subtitle}>手动填表与 AI 辅助填表的申请都在这里，草稿需本人确认后才会提交审核</div>
        </div>
        <Space>
          <Button onClick={load}>刷新</Button>
          <Button type="primary" onClick={() => navigate(`/flows/${flowId}/apply`)}>新建申请</Button>
        </Space>
      </div>
      <Card style={styles.card}>
        <Segmented options={filters} value={status} onChange={(v) => setStatus(String(v))} style={{ marginBottom: 16 }} />
        <Table rowKey="id" columns={columns} dataSource={rows} loading={loading}
               scroll={{ x: 1200 }} pagination={{ pageSize: 10, showSizeChanger: false }}
               locale={{ emptyText: '暂无申请记录' }} />
      </Card>

      <Drawer title={detail ? `申请详情 · ${detail.id}` : '申请详情'} width={720}
              open={drawerOpen} onClose={() => setDrawerOpen(false)}>
        {detail && config && <FlowApplicationDetail application={detail} config={config} />}
      </Drawer>

      <Modal title="审核结果" open={Boolean(passInfo)} onCancel={() => setPassInfo(null)} footer={null} width={360}>
        {passInfo && (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: 1, marginBottom: 12, fontFamily: 'monospace' }}>
              {passInfo.pass_no || detail?.id}
            </div>
            {passInfo.qr_svg
              ? <img src={passInfo.qr_svg} alt="二维码" style={{ width: 220, height: 220, background: '#FFFFFF', padding: 8, borderRadius: 8 }} />
              : <div style={{ fontSize: 12, color: '#94A3B8' }}>该流程未配置二维码输出</div>}
            {passInfo.payload && <div style={{ fontSize: 11, color: '#5E6E88', marginTop: 10, wordBreak: 'break-all' }}>凭证内容：{passInfo.payload}</div>}
          </div>
        )}
      </Modal>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  page: { padding: 24, width: '100%' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 },
  title: { fontSize: 20, fontWeight: 600, color: '#E4EBF5', margin: 0 },
  subtitle: { fontSize: 12, color: '#5E6E88', marginTop: 6 },
  card: { background: '#0D1525', border: '1px solid #1A2748' },
};

export default FlowMyPage;
