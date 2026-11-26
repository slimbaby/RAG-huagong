import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Drawer, Input, Modal, Popconfirm, Segmented, Space, Table, Tag, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useAuthStore } from '../stores/authStore';
import ApplicationDetail from '../components/Entry/ApplicationDetail';
import {
  EntryApplication, STATUS_COLORS, STATUS_LABELS, sourceLabel,
  listReviewEntries, reviewEntryApplication,
} from '../api/entry';

const FILTERS = [
  { label: '待审核', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已驳回', value: 'rejected' },
  { label: '全部', value: '' },
];

const EntryReviewPage: React.FC = () => {
  const roleId = useAuthStore((s) => s.role_id);
  const [rows, setRows] = useState<EntryApplication[]>([]);
  const [status, setStatus] = useState<string>('pending');
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<EntryApplication | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [rejectTarget, setRejectTarget] = useState<EntryApplication | null>(null);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listReviewEntries(status);
      setRows(res.applications || []);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载待审核列表失败');
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => { if (roleId === 'admin') load(); }, [load, roleId]);

  const doReview = async (id: string, approved: boolean, text: string) => {
    setSubmitting(true);
    try {
      await reviewEntryApplication(id, approved, text);
      message.success(approved ? '已通过审核' : '已驳回申请');
      setRejectTarget(null);
      setComment('');
      setDrawerOpen(false);
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '审核操作失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (roleId !== 'admin') {
    return (
      <div style={styles.page}>
        <Alert type="error" showIcon message="无权访问" description="待审核页面仅管理员（admin）可见，后端审核接口同样会校验角色。" />
      </div>
    );
  }

  const columns: ColumnsType<EntryApplication> = [
    { title: '申请单号', dataIndex: 'id', width: 170, render: (v: string) => <span style={{ fontFamily: 'monospace' }}>{v}</span> },
    {
      title: '来源', dataIndex: 'source', width: 120,
      render: (_: string, r: EntryApplication) => (
        <Tag color={r.assisted_by_ai ? 'blue' : 'default'}>{sourceLabel(r)}</Tag>
      ),
    },
    { title: '申请人账号', dataIndex: 'applicant_username', width: 130 },
    { title: '司机', dataIndex: 'driver_name', width: 90, render: (v: string) => v || '—' },
    { title: '车牌号', dataIndex: 'plate_no', width: 110, render: (v: string) => v || '—' },
    { title: '申请公司', dataIndex: 'company_name', ellipsis: true },
    { title: '入园时间', key: 'entry_time', width: 150, render: (_, r) => r.form_data?.entry_time || '—' },
    {
      title: '状态', dataIndex: 'status', width: 110,
      render: (v: string) => <Tag color={STATUS_COLORS[v]}>{STATUS_LABELS[v] || v}</Tag>,
    },
    {
      title: '审核操作', key: 'action', width: 200, fixed: 'right',
      render: (_, r) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => { setDetail(r); setDrawerOpen(true); }}>查看详情</Button>
          {r.status === 'pending' && (
            <>
              <Popconfirm title="确认通过该入园申请？" onConfirm={() => doReview(r.id, true, '')}>
                <Button type="link" size="small">通过</Button>
              </Popconfirm>
              <Button type="link" size="small" danger onClick={() => { setRejectTarget(r); setComment(''); }}>驳回</Button>
            </>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <div>
          <h2 style={styles.title}>入园申请审核</h2>
          <div style={styles.subtitle}>仅 admin 可见；AI 助手不参与审批，放行决定由审核人在此作出</div>
        </div>
        <Button onClick={load}>刷新</Button>
      </div>

      <Card style={styles.card}>
        <Segmented options={FILTERS} value={status} onChange={(v) => setStatus(String(v))} style={{ marginBottom: 16 }} />
        <Table
          rowKey="id"
          columns={columns}
          dataSource={rows}
          loading={loading}
          scroll={{ x: 1200 }}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          locale={{ emptyText: '暂无申请单' }}
        />
      </Card>

      <Drawer
        title={detail ? `审核申请 · ${detail.id}` : '审核申请'}
        width={720}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        extra={detail?.status === 'pending' && (
          <Space>
            <Button danger onClick={() => { setRejectTarget(detail); setComment(''); }}>驳回</Button>
            <Button type="primary" loading={submitting} onClick={() => doReview(detail.id, true, '')}>审核通过</Button>
          </Space>
        )}
      >
        {detail && <ApplicationDetail application={detail} />}
      </Drawer>

      <Modal
        title={`驳回申请 ${rejectTarget?.id || ''}`}
        open={Boolean(rejectTarget)}
        onCancel={() => setRejectTarget(null)}
        onOk={() => rejectTarget && doReview(rejectTarget.id, false, comment)}
        okText="确认驳回"
        okButtonProps={{ danger: true, disabled: !comment.trim() }}
        confirmLoading={submitting}
      >
        <div style={{ fontSize: 12, color: '#94A3B8', marginBottom: 8 }}>请填写驳回原因，司机可在「我的申请」中查看：</div>
        <Input.TextArea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="如：危化品运输证已过期，请重新上传" />
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

export default EntryReviewPage;
