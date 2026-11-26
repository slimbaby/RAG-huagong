import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Form, Input, Select, Space, Spin, Tag, message } from 'antd';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import type { FlowApplication, FlowField, FlowRenderConfig } from '../api/flow';
import {
  createFlowApplication, getFlowApplication, submitFlowApplication,
  updateFlowApplication, uploadFlowAttachment, validateFlowForm,
} from '../api/flow';
import { prefillKey, useFlowStore } from '../stores/flowStore';
import { useChatStore } from '../stores/chatStore';

interface UploaderProps {
  flowId: string;
  kinds: FlowRenderConfig['attachments'];
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  disabled?: boolean;
}

const AttachmentUploader: React.FC<UploaderProps> = ({ flowId, kinds, value, onChange, disabled }) => {
  const fileRef = useRef<HTMLInputElement>(null);
  const pendingKind = useRef<string>('');
  const [uploading, setUploading] = useState<string | null>(null);

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
      const res = await uploadFlowAttachment(flowId, file, kind);
      onChange({ ...value, [kind]: res.id });
      message.success(`${res.label} 上传成功`);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '上传失败，请检查格式与大小');
    } finally {
      setUploading(null);
    }
  };

  return (
    <div>
      <input ref={fileRef} type="file" accept=".jpg,.jpeg,.png,.webp,.pdf" style={{ display: 'none' }} onChange={handleFile} />
      <div style={styles.attGrid}>
        {kinds.map((k) => {
          const done = Boolean(value[k.key]);
          return (
            <div key={k.key} style={styles.attCard}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 13, color: '#E4EBF5' }}>{k.label}</span>
                {done ? <Tag color="success">已上传</Tag> : <Tag>{k.required ? '必传' : '选传'}</Tag>}
              </div>
              <div style={{ fontSize: 11, color: '#5E6E88', margin: '6px 0 10px' }}>
                {done ? '附件已保存' : (k.accept || 'JPG / PNG / WEBP / PDF')}
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

const renderField = (f: FlowField) => {
  if (f.type === 'enum' && f.options?.length) {
    return <Select placeholder={f.placeholder || '请选择'} options={f.options.map((o) => ({ value: o, label: o }))} />;
  }
  return <Input placeholder={f.placeholder || `请填写${f.label}`} />;
};

const isVisible = (f: FlowField, values: Record<string, any>) => {
  const rule = f.visible_when;
  if (!rule || !rule.field) return true;
  const v = values?.[rule.field];
  if (rule.in) return rule.in.includes(v);
  if (rule.eq !== undefined) return v === rule.eq;
  return true;
};

/** 通用申请表单：字段、分组、必填、附件类型、状态名全部由后端配置下发。 */
const FlowApplyPage: React.FC = () => {
  const { flowId = '' } = useParams<{ flowId: string }>();
  const [form] = Form.useForm();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const editId = searchParams.get('id') || '';
  const [attachments, setAttachments] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('draft');
  const [assistedByAi, setAssistedByAi] = useState(false);
  const configs = useFlowStore((s) => s.configs);
  const errors = useFlowStore((s) => s.errors);
  const loadConfig = useFlowStore((s) => s.loadConfig);
  const config = configs[flowId];
  const formValues = Form.useWatch([], form) || {};

  useEffect(() => { loadConfig(flowId); }, [flowId, loadConfig]);

  // 聊天确认卡 → 表单预填：字段只在浏览器 sessionStorage 里短暂流转
  useEffect(() => {
    if (searchParams.get('prefill') !== '1') return;
    try {
      const raw = sessionStorage.getItem(prefillKey(flowId));
      if (!raw) return;
      const slots = JSON.parse(raw) as Record<string, any>;
      const { attachments: att, ...fields } = slots;
      form.setFieldsValue(fields);
      setAttachments(att || {});
      setAssistedByAi(true);
      sessionStorage.removeItem(prefillKey(flowId));
      message.info('已带入 AI 整理的字段，请逐项核对后再提交');
    } catch { /* ignore */ }
  }, [searchParams, form, flowId]);

  useEffect(() => {
    if (!editId || !flowId) return;
    setLoading(true);
    getFlowApplication(flowId, editId)
      .then((app: FlowApplication) => {
        form.setFieldsValue(app.form_data || {});
        setAttachments(app.form_data?.attachments || app.attachments || {});
        setStatus(app.status);
        setAssistedByAi(Boolean(app.assisted_by_ai));
      })
      .catch((e) => message.error(e?.response?.data?.detail || '加载申请单失败'))
      .finally(() => setLoading(false));
  }, [editId, form, flowId]);

  const buildPayload = useCallback((values: any): Record<string, any> => ({ ...values, attachments }), [attachments]);

  const handleSave = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      if (editId) {
        await updateFlowApplication(flowId, editId, buildPayload(values));
        message.success('草稿已保存');
      } else {
        const app = await createFlowApplication(flowId, buildPayload(values), assistedByAi);
        message.success('草稿已创建，可在「我的申请」继续编辑');
        navigate(`/flows/${flowId}/apply?id=${app.id}`, { replace: true });
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  const handleSubmit = async () => {
    const values = await form.validateFields();
    const requiredKinds = (config?.attachments || []).filter((k) => k.required !== false);
    const missingKinds = requiredKinds.filter((k) => !attachments[k.key]);
    if (missingKinds.length) {
      message.warning('请先上传：' + missingKinds.map((k) => k.label).join('、'));
      return;
    }
    setSaving(true);
    try {
      const payload = buildPayload(values);
      // 必填 / 时间 / 上限等确定性规则全部由后端按已发布配置执行
      const check = await validateFlowForm(flowId, payload, true);
      if (!check.complete) {
        message.warning('还有问题：' + [...check.missing, ...check.errors].join('；'));
        return;
      }
      let id = editId;
      if (id) await updateFlowApplication(flowId, id, payload);
      else id = (await createFlowApplication(flowId, payload, assistedByAi)).id;
      await submitFlowApplication(flowId, id);
      // 本人提交成功后结束对话侧的流程状态：清槽位/流程标记，并撤下历史回答下面
      // 已失效的核对卡，避免回到聊天后每轮问答都重出一张「去表单提交」卡片。
      // 只有 submit 成功才清；保存草稿 / 提交失败时仍要保留 AI 收集的信息。
      try { useChatStore.getState().clearFlowState(); } catch { /* 聊天页未挂载时忽略 */ }
      message.success('申请已提交，等待管理员审核');
      navigate(`/flows/${flowId}/my`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败，请检查必填项');
    } finally { setSaving(false); }
  };

  if (errors[flowId]) {
    return <div style={styles.page}><Alert type="error" showIcon message="流程配置不可用" description={errors[flowId]} /></div>;
  }
  if (!config) return <div style={styles.page}><Spin size="large" /></div>;

  const readonly = status !== 'draft';
  const groups = config.groups?.length ? config.groups : [{ key: 'default', label: '申请信息' }];
  const ungrouped = (config.fields || []).filter((f) => !groups.some((g) => g.key === (f.group || 'default')));
  const sectionList = [
    ...groups.map((g) => ({ ...g, fields: (config.fields || []).filter((f) => (f.group || 'default') === g.key) })),
    ...(ungrouped.length ? [{ key: '__other', label: '其他信息', fields: ungrouped }] : []),
  ].filter((g) => g.fields.length > 0);

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <div>
          <h2 style={styles.title}>{editId ? `核对 / 编辑「${config.name}」` : config.name}</h2>
          <div style={styles.subtitle}>
            {editId ? `申请单 ${editId}` : '按表单逐项填写并上传材料，确认后提交审核'} · 配置 v{config.version}
          </div>
        </div>
        <Space>
          {assistedByAi && <Tag color="blue">AI 辅助填表</Tag>}
          {editId && <Tag color="processing">{config.state_labels?.[status] || status}</Tag>}
          <Button onClick={() => navigate(`/flows/${flowId}/my`)}>我的申请</Button>
        </Space>
      </div>

      <Card style={styles.card}>
        <Spin spinning={loading}>
          {readonly && <Alert type="info" showIcon message="该申请单已提交或已审核，内容不可再修改" style={{ marginBottom: 16 }} />}
          {assistedByAi && !readonly && (
            <Alert type="warning" showIcon message="这是 AI 助手根据对话整理的字段，请逐项核对后再确认提交" style={{ marginBottom: 16 }} />
          )}
          <Form form={form} layout="vertical" disabled={readonly} requiredMark>
            {sectionList.map((g) => (
              <div key={g.key}>
                <h3 style={styles.sectionTitle}>{g.label}</h3>
                <div style={styles.fieldGrid}>
                  {g.fields.filter((f) => isVisible(f, formValues)).map((f) => (
                    <Form.Item
                      key={f.key}
                      name={f.key}
                      label={f.label}
                      rules={f.required === false ? [] : [{ required: true, message: `请填写${f.label}` }]}
                    >
                      {renderField(f)}
                    </Form.Item>
                  ))}
                </div>
              </div>
            ))}
            {(config.attachments || []).length > 0 && (
              <>
                <h3 style={styles.sectionTitle}>照片与证件材料</h3>
                <Form.Item>
                  <AttachmentUploader flowId={flowId} kinds={config.attachments} value={attachments}
                                      onChange={setAttachments} disabled={readonly} />
                </Form.Item>
              </>
            )}
          </Form>
          {!readonly && (
            <Space style={{ marginTop: 8 }} wrap>
              <Button onClick={handleSave} loading={saving}>保存草稿</Button>
              <Button type="primary" onClick={handleSubmit} loading={saving}>确认提交申请</Button>
              <span style={{ fontSize: 12, color: '#5E6E88' }}>提交后将进入管理员待审核列表，AI 不会代你提交</span>
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

export default FlowApplyPage;
