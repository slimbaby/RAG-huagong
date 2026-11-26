import React, { useCallback, useEffect, useState } from 'react';
import { Button, Card, Drawer, Modal, Segmented, Space, Table, Tag, message, Popconfirm } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import ApplicationDetail from '../components/Entry/ApplicationDetail';
import {
  EntryApplication, EntryPass, STATUS_COLORS, STATUS_LABELS, sourceLabel,
  cancelEntryApplication, getEntryPass, listMyEntryApplications,
  reopenEntryApplication, submitEntryApplication,
} from '../api/entry';

const FILTERS = [
  { label: '全部', value: '' },
  { label: '待确认草稿', value: 'draft' },
  { label: '待审核', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已驳回', value: 'rejected' },
];

const EntryMyPage: React.FC = () => {
  const navigate = useNavigate();
  const [rows, setRows] = useState<EntryApplication[]>([]);
  const [status, setStatus] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<EntryApplication | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [passInfo, setPassInfo] = useState<EntryPass | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listMyEntryApplications(status || undefined);
      setRows(res.applications || []);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载申请列表失败');
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => { load(); }, [load]);

  const doSubmit = async (id: string) => {
    try {
      await submitEntryApplication(id);
      message.success('已确认提交，等待管理员审核');
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败');
    }
  };

  const doCancel = async (id: string) => {
    try {
      await cancelEntryApplication(id);
      message.success('申请已撤回');
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '撤回失败');
    }
  };

  const doReopen = async (id: string) => {
    try {
      await reopenEntryApplication(id);
      message.success('已回到草稿，可修改后重新提交');
      load();
      navigate(`/entry/apply?id=${id}`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重提失败');
    }
  };

  const showPass = async (id: string) => {
    try {
      setPassInfo(await getEntryPass(id));
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '通行证获取失败');
    }
  };

  const openDetail = (row: EntryApplication) => {
    setDetail(row);
    setDrawerOpen(true);
  };

  const columns: ColumnsType<EntryApplication> = [
    { title: '申请单号', dataIndex: 'id', width: 170, render: (v: string) => <span style={{ fontFamily: 'monospace' }}>{v}</span> },
    {
      title: '来源', dataIndex: 'source', width: 120,
      render: (_: string, r: EntryApplication) => (
        <Tag color={r.assisted_by_ai ? 'blue' : 'default'}>{sourceLabel(r)}</Tag>
      ),
    },
    { title: '司机', dataIndex: 'driver_name', width: 90, render: (v: string) => v || '—' },
    { title: '车牌号', dataIndex: 'plate_no', width: 110, render: (v: string) => v || '—' },
    { title: '申请公司', dataIndex: 'company_name', ellipsis: true },
    {
      title: '入园时间', key: 'entry_time', width: 150,
      render: (_, r) => r.form_data?.entry_time || '—',
    },
    {
      title: '状态', dataIndex: 'status', width: 120,
      render: (v: string) => <Tag color={STATUS_COLORS[v]}>{STATUS_LABELS[v] || v}</Tag>,
    },
    {
      title: '操作', key: 'action', width: 250, fixed: 'right',
      render: (_, r) => (
        <Space size={4} wrap>
          <Button type="link" size="small" onClick={() => openDetail(r)}>详情</Button>
          {r.status === 'draft' && (
            <>
              <Button type="link" size="small" onClick={() => navigate(`/entry/apply?id=${r.id}`)}>核对 / 编辑</Button>
              <Popconfirm title="确认提交后将进入管理员待审核列表" onConfirm={() => doSubmit(r.id)}>
                <Button type="link" size="small">确认提交</Button>
              </Popconfirm>
            </>
          )}
          {(r.status === 'draft' || r.status === 'pending') && (
            <Popconfirm title="确定撤回该申请吗？" onConfirm={() => doCancel(r.id)}>
              <Button type="link" size="small" danger>撤回</Button>
            </Popconfirm>
          )}
          {r.status === 'rejected' && (
            <Popconfirm title="把原单退回草稿，修改后可再次提交（保留单号与审核记录）" onConfirm={() => doReopen(r.id)}>
              <Button type="link" size="small">改原单重提</Button>
            </Popconfirm>
          )}
          {r.status === 'approved' && (
            <Button type="link" size="small" onClick={() => showPass(r.id)}>通行证</Button>
          )}
          {r.status === 'rejected' && r.review_comment && (
            <Button type="link" size="small" onClick={() => openDetail(r)}>查看驳回原因</Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <div>
          <h2 style={styles.title}>我的入园申请</h2>
          <div style={styles.subtitle}>手动填表与 AI 辅助填表的申请都在这里，草稿需本人确认后才会提交审核</div>
        </div>
        <Space>
          <Button onClick={load}>刷新</Button>
          <Button type="primary" onClick={() => navigate('/entry/apply')}>新建申请</Button>
        </Space>
      </div>

      <Card style={styles.card}>
        <Segmented
          options={FILTERS}
          value={status}
          onChange={(v) => setStatus(String(v))}
          style={{ marginBottom: 16 }}
        />
        <Table
          rowKey="id"
          columns={columns}
          dataSource={rows}
          loading={loading}
          scroll={{ x: 1100 }}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          locale={{ emptyText: '暂无申请记录' }}
        />
      </Card>

      <Drawer
        title={detail ? `申请详情 · ${detail.id}` : '申请详情'}
        width={720}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      >
        {detail && <ApplicationDetail application={detail} />}
      </Drawer>

      <Modal
        title="入园通行证"
        open={Boolean(passInfo)}
        onCancel={() => setPassInfo(null)}
        footer={null}
        width={360}
      >
        {passInfo && (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: 1, marginBottom: 12, fontFamily: 'monospace' }}>
              {passInfo.pass_no}
            </div>
            {passInfo.qr_svg ? (
              <img src={passInfo.qr_svg} alt="通行证二维码" style={{ width: 220, height: 220, background: '#FFFFFF', padding: 8, borderRadius: 8 }} />
            ) : (
              <div style={{ color: '#EF4444', fontSize: 12 }}>二维码生成失败，请联系管理员</div>
            )}
            <div style={{ fontSize: 11, color: '#5E6E88', marginTop: 10, wordBreak: 'break-all' }}>
              凭证内容：{passInfo.payload}
            </div>
            <div style={{ fontSize: 11, color: '#5E6E88', marginTop: 4 }}>
              签发时间：{passInfo.issued_at?.slice(0, 19).replace('T', ' ') || '—'}
            </div>
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

export default EntryMyPage;
