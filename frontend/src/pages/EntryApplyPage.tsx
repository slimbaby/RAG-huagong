import React, { useEffect, useRef, useState } from 'react';
import {
  Alert, Button, Card, Form, Input, Select, Space, Spin, Tag, message,
} from 'antd';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ATTACHMENT_KINDS, EntryFormData, createEntryApplication, getEntryApplication,
  submitEntryApplication, updateEntryApplication, uploadEntryAttachment,
  validateEntryForm,
} from '../api/entry';
import { ENTRY_PREFILL_KEY } from '../components/Entry/EntryConfirmCard';

const TIME_RULE = {
  pattern: /^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$/,
  message: '请填 YYYY-MM-DD HH:mm，如 2026-11-26 08:00',
};

interface AttachmentUploaderProps {
  value?: Record<string, string>;
  onChange?: (v: Record<string, string>) => void;
  disabled?: boolean;
}

const AttachmentUploader: React.FC<AttachmentUploaderProps> = ({ value = {}, onChange, disabled }) => {
  const fileRef = useRef<HTMLInputElement>(null);
  const pendingKind = useRef<string>('');
  const [uploading, setUploading] = useState<string | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});

  const pick = (kind: string) => {
    if (disabled) return;
    pendingKind.current = kind;
    fileRef.current?.click();
  };

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    const kind = pendingKind.current;
    if (!file || !kind) return;
    setUploading(kind);
    try {
      const res = await uploadEntryAttachment(file, kind);
      onChange?.({ ...value, [kind]: res.id });
      setNames((n) => ({ ...n, [kind]: res.filename }));
      message.success(`${res.label} 上传成功`);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '上传失败，请检查格式与大小');
    } finally {
      setUploading(null);
    }
  };

  return (
    <div>
      <input
        ref={fileRef}
        type="file"
        accept=".jpg,.jpeg,.png,.webp,.pdf"
        style={{ display: 'none' }}
        onChange={handleFile}
      />
      <div style={styles.attGrid}>
        {ATTACHMENT_KINDS.map((k) => {
          const done = Boolean(value[k.key]);
          return (
            <div key={k.key} style={styles.attCard}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 13, color: '#E4EBF5' }}>{k.label}</span>
                {done ? <Tag color="success">已上传</Tag> : <Tag>待上传</Tag>}
              </div>
              <div style={{ fontSize: 11, color: '#5E6E88', margin: '6px 0 10px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {names[k.key] || (done ? '附件已保存' : 'JPG / PNG / WEBP / PDF，≤10MB')}
              </div>
              <Button size="small" block loading={uploading === k.key} disabled={disabled} onClick={() => pick(k.key)}>
                {done ? '重新上传' : '选择文件'}
              </Button>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const EntryApplyPage: React.FC = () => {
  const [form] = Form.useForm();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const editId = searchParams.get('id') || '';
  const [attachments, setAttachments] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('draft');
  const [assistedByAi, setAssistedByAi] = useState(false);

  // 聊天确认卡 → 表单预填：字段只在浏览器 sessionStorage 里短暂流转
  useEffect(() => {
    if (searchParams.get('prefill') !== '1') return;
    try {
      const raw = sessionStorage.getItem(ENTRY_PREFILL_KEY);
      if (!raw) return;
      const slots = JSON.parse(raw) as EntryFormData;
      const { attachments: att, ...fields } = slots;
      form.setFieldsValue(fields);
      setAttachments(att || {});
      setAssistedByAi(true);
      sessionStorage.removeItem(ENTRY_PREFILL_KEY);
      message.info('已带入 AI 整理的字段，请逐项核对后再提交');
    } catch {
      // ignore
    }
  }, [searchParams, form]);

  useEffect(() => {
    if (!editId) return;
    setLoading(true);
    getEntryApplication(editId)
      .then((app) => {
        form.setFieldsValue(app.form_data || {});
        setAttachments(app.form_data?.attachments || {});
        setStatus(app.status);
        setAssistedByAi(Boolean(app.assisted_by_ai));
      })
      .catch((e) => message.error(e?.response?.data?.detail || '加载申请单失败'))
      .finally(() => setLoading(false));
  }, [editId, form]);

  const buildPayload = (values: any): EntryFormData => ({ ...values, attachments });

  const handleSave = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      if (editId) {
        await updateEntryApplication(editId, buildPayload(values));
        message.success('草稿已保存');
      } else {
        const app = await createEntryApplication(buildPayload(values), assistedByAi);
        message.success('草稿已创建，可在「我的申请」继续编辑');
        navigate(`/entry/apply?id=${app.id}`, { replace: true });
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleSubmit = async () => {
    const values = await form.validateFields();
    if (ATTACHMENT_KINDS.some((k) => !attachments[k.key])) {
      message.warning('请先上传全部五类车辆照片与证件');
      return;
    }
    setSaving(true);
    try {
      const payload = buildPayload(values);
      // 前端与后端共用同一套确定性规则（必填 / 重量上限 / 时间先后）
      const check = await validateEntryForm(payload, true);
      if (!check.complete) {
        message.warning('还有问题：' + [...check.missing, ...check.errors].join('；'));
        return;
      }
      let id = editId;
      if (id) await updateEntryApplication(id, payload);
      else id = (await createEntryApplication(payload, assistedByAi)).id;
      await submitEntryApplication(id);
      message.success('申请已提交，等待管理员审核');
      navigate('/entry/my');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败，请检查必填项');
    } finally {
      setSaving(false);
    }
  };

  const readonly = status !== 'draft';

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <div>
          <h2 style={styles.title}>{editId ? '核对 / 编辑入园申请' : '司机入园预约申请'}</h2>
          <div style={styles.subtitle}>
            {editId ? `申请单 ${editId}` : '填写车辆、司机与运输信息，上传五类照片证件后确认提交'}
          </div>
        </div>
        <Space>
          {assistedByAi && <Tag color="blue">AI 辅助填表</Tag>}
          {editId && <Tag color="processing">{status}</Tag>}
          <Button onClick={() => navigate('/entry/my')}>我的申请</Button>
        </Space>
      </div>

      <Card style={styles.card}>
        <Spin spinning={loading}>
          {readonly && (
            <Alert
              type="info"
              showIcon
              message="该申请单已提交或已审核，内容不可再修改"
              style={{ marginBottom: 16 }}
            />
          )}
          {assistedByAi && !readonly && (
            <Alert
              type="warning"
              showIcon
              message="这是 AI 助手根据对话整理的字段，请逐项核对（尤其证件照片与身份证号）后再确认提交"
              style={{ marginBottom: 16 }}
            />
          )}

          <Form form={form} layout="vertical" disabled={readonly} requiredMark>
            <h3 style={styles.sectionTitle}>司机信息</h3>
            <div style={styles.fieldGrid}>
              <Form.Item name="driver_name" label="司机姓名" rules={[{ required: true, message: '请填写司机姓名' }]}>
                <Input placeholder="张三" />
              </Form.Item>
              <Form.Item name="gender" label="性别" rules={[{ required: true, message: '请选择性别' }]}>
                <Select placeholder="请选择" options={[{ value: '男', label: '男' }, { value: '女', label: '女' }]} />
              </Form.Item>
              <Form.Item name="id_no" label="身份证号" rules={[{ required: true, message: '请填写身份证号' }]}>
                <Input placeholder="18 位身份证号码" />
              </Form.Item>
              <Form.Item name="phone" label="手机号" rules={[{ required: true, message: '请填写手机号' }]}>
                <Input placeholder="13800000000" />
              </Form.Item>
            </div>

            <h3 style={styles.sectionTitle}>车辆信息</h3>
            <div style={styles.fieldGrid}>
              <Form.Item name="plate_no" label="车牌号" rules={[{ required: true, message: '请填写车牌号' }]}>
                <Input placeholder="鲁A12345" />
              </Form.Item>
              <Form.Item name="vehicle_type" label="车辆类型" rules={[{ required: true, message: '请填写车辆类型' }]}>
                <Input placeholder="如 罐式货车 / 槽罐车" />
              </Form.Item>
              <Form.Item name="load_capacity" label="车辆承重" rules={[{ required: true, message: '请填写车辆承重' }]}>
                <Input placeholder="如 30吨" />
              </Form.Item>
            </div>

            <h3 style={styles.sectionTitle}>车辆照片与证件</h3>
            <Form.Item>
              <AttachmentUploader value={attachments} onChange={setAttachments} disabled={readonly} />
            </Form.Item>

            <h3 style={styles.sectionTitle}>运输与申请信息</h3>
            <div style={styles.fieldGrid}>
              <Form.Item name="company_name" label="申请公司名称" rules={[{ required: true, message: '请填写公司名称' }]}>
                <Input placeholder="XX化工有限公司" />
              </Form.Item>
              <Form.Item name="chemical_name" label="运输化学品名称" rules={[{ required: true, message: '请填写化学品名称' }]}>
                <Input placeholder="如 甲醇" />
              </Form.Item>
              <Form.Item name="chemical_type" label="化学品类型" rules={[{ required: true, message: '请填写化学品类型' }]}>
                <Input placeholder="如 易燃液体" />
              </Form.Item>
              <Form.Item name="weight_ton" label="运输重量（吨）" rules={[{ required: true, message: '请填写运输重量' }]}>
                <Input placeholder="仅填数字，如 20" />
              </Form.Item>
              <Form.Item name="entry_time" label="入园时间" rules={[{ required: true, message: '请填写入园时间' }, TIME_RULE]}>
                <Input placeholder="2026-11-26 08:00" />
              </Form.Item>
              <Form.Item name="exit_time" label="出园时间" rules={[{ required: true, message: '请填写出园时间' }, TIME_RULE]}>
                <Input placeholder="2026-11-26 18:00" />
              </Form.Item>
            </div>
          </Form>

          {!readonly && (
            <Space style={{ marginTop: 8 }}>
              <Button onClick={handleSave} loading={saving}>保存草稿</Button>
              <Button type="primary" onClick={handleSubmit} loading={saving}>确认提交申请</Button>
              <span style={{ fontSize: 12, color: '#5E6E88' }}>提交后将进入管理员待审核列表</span>
            </Space>
          )}
        </Spin>
      </Card>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  page: { padding: 24, maxWidth: 1080, width: '100%', margin: '0 auto' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 },
  title: { fontSize: 20, fontWeight: 600, color: '#E4EBF5', margin: 0 },
  subtitle: { fontSize: 12, color: '#5E6E88', marginTop: 6 },
  card: { background: '#0D1525', border: '1px solid #1A2748' },
  sectionTitle: { fontSize: 14, fontWeight: 600, color: '#94A3B8', margin: '8px 0 16px', paddingBottom: 8, borderBottom: '1px solid #1A2748' },
  fieldGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '0 16px' },
  attGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 },
  attCard: { border: '1px solid #1A2748', borderRadius: 8, padding: 12, background: '#0A1224' },
};

export default EntryApplyPage;
