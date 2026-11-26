import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Form, Input, InputNumber, Modal, Row, Select,
  Space, Spin, Table, Tabs, Tag, message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  bootstrapFlows, getConfigAudit, getDraft, getFlowTemplate, getRollout, getTestCases,
  getVersionDiff, listFlows, listHooks, listVersions, previewConfig, publishFlow,
  rollbackFlow, runTestCases, saveDraft, saveTestCases, setRollout, validateConfig,
} from '../api/flow';
import { useAuthStore } from '../stores/authStore';
import { useFlowStore } from '../stores/flowStore';

const SECTIONS = [
  { key: 'meta', label: '基本信息', hint: 'flow_id / name / description / version' },
  { key: 'groups', label: '字段分组', hint: '[{key,label}]' },
  { key: 'fields', label: '表单字段', hint: '[{key,label,group,type,required,...}]' },
  { key: 'intent', label: '意图规则与用例', hint: 'rule_pipeline / domain_words / classifier / test_cases' },
  { key: 'attachments', label: '附件类型', hint: '[{key,label,required,reuse_policy}]' },
  { key: 'workflow', label: '状态与流转', hint: 'state_labels / allowed_transitions / pass' },
  { key: 'session', label: '会话策略', hint: 'session_prefix / ttl_hours / version_pin' },
  { key: 'entry_points', label: '菜单与快捷入口', hint: '[{type,key,label,path,visible_roles,icon,order}]' },
  { key: 'card', label: '确认卡', hint: 'title / field_keys / prefill_target' },
  { key: 'prompts', label: '话术与 SOP', hint: 'system_prompt / ask_hints / examples' },
  { key: 'hooks', label: '命名扩展点', hint: '只能引用白名单内的 hook，禁止表达式 / 脚本' },
  { key: 'permissions', label: '权限', hint: 'applicant_roles / reviewer_roles / scope' },
];

const FIELD_TYPES = ['string', 'number', 'date', 'datetime', 'enum', 'phone', 'idcard', 'plate', 'file', 'ref'];

const FlowConfigPage: React.FC = () => {
  const roleId = useAuthStore((s) => s.role_id);
  const refreshEntryPoints = useFlowStore((s) => s.loadEntryPoints);
  const [flows, setFlows] = useState<any[]>([]);
  const [flowId, setFlowId] = useState('');
  const [definition, setDefinition] = useState<Record<string, any>>({});
  const [revision, setRevision] = useState(0);
  const [publishedVersion, setPublishedVersion] = useState(0);
  const [updatedBy, setUpdatedBy] = useState('');
  const [section, setSection] = useState('meta');
  const [jsonText, setJsonText] = useState('{}');
  const [parseError, setParseError] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [validation, setValidation] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [diff, setDiff] = useState<any>(null);
  const [rollout, setRolloutState] = useState<any>({ mode: 'full', version: 0, users: [], groups: [], observe_days: 1 });
  const [hooks, setHooks] = useState<any[]>([]);
  const [cases, setCases] = useState<any[]>([]);
  const [caseResult, setCaseResult] = useState<any>(null);
  const [audit, setAudit] = useState<any[]>([]);
  const [publishNote, setPublishNote] = useState('');
  const [newOpen, setNewOpen] = useState(false);
  const [newFlow, setNewFlow] = useState({ flow_id: '', name: '', keyword: '' });

  const role = String(roleId || '');
  const canPublish = ['admin', 'config_publisher'].includes(role);
  const canEdit = ['admin', 'config_editor', 'config_publisher'].includes(role);

  const loadFlows = useCallback(async () => {
    try {
      const res = await listFlows();
      setFlows(res.flows || []);
      return res.flows || [];
    } catch (e: any) { message.error(e?.response?.data?.detail || '流程列表加载失败'); return []; }
  }, []);

  const loadDraft = useCallback(async (id: string) => {
    if (!id) return;
    setLoading(true);
    try {
      const res = await getDraft(id);
      const def = res.definition || {};
      setDefinition(def);
      setRevision(Number(res.draft_revision || 0));
      setPublishedVersion(Number(res.published_version || 0));
      setUpdatedBy(res.updated_by || '');
      setSection('meta');
      setJsonText(JSON.stringify(def.meta || {}, null, 2));
      setParseError('');
      setValidation(null);
    } catch (e: any) { message.error(e?.response?.data?.detail || '草稿加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    (async () => {
      const list = await loadFlows();
      if (list.length) {
        const first = list[0].flow_id;
        setFlowId(first);
        await loadDraft(first);
      }
    })();
  }, [loadFlows, loadDraft]);

  const wrap = async (fn: () => Promise<any>, ok?: string) => {
    setBusy(true);
    try { const data = await fn(); if (ok) message.success(ok); return data; }
    catch (e: any) {
      const d = e?.response?.data?.detail;
      message.error(typeof d === 'string' ? d : (d?.message || '操作失败'));
      return null;
    } finally { setBusy(false); }
  };

  const onSectionChange = (key: string) => {
    setSection(key);
    setJsonText(JSON.stringify(definition[key] ?? (key === 'fields' || key === 'groups' ? [] : {}), null, 2));
    setParseError('');
  };

  const onJsonChange = (text: string) => {
    setJsonText(text);
    try {
      const value = JSON.parse(text);
      setParseError('');
      setDefinition((d) => ({ ...d, [section]: value }));
    } catch (e: any) { setParseError(e.message || 'JSON 格式错误'); }
  };

  const doSaveDraft = async () => {
    if (parseError) { message.warning('当前配置段 JSON 有语法错误，请先修正'); return; }
    const res = await wrap(() => saveDraft(flowId, definition, revision), '草稿已保存（含 dry-run 自检）');
    if (!res) return;
    setRevision(Number(res.draft_revision));
    if (res.validation) setValidation(res.validation);
    loadFlows();
  };

  const doCreate = async () => {
    if (!/^[a-z][a-z0-9_]{1,63}$/.test(newFlow.flow_id)) {
      message.warning('flow_id 必须是小写字母开头、只含小写字母/数字/下划线，长度 2–64'); return;
    }
    const tpl = await wrap(() => getFlowTemplate(newFlow.flow_id, newFlow.name, newFlow.keyword));
    if (!tpl) return;
    const saved = await wrap(() => saveDraft(newFlow.flow_id, tpl.definition, 0), '新流程草稿已创建');
    if (!saved) return;
    setNewOpen(false);
    const list = await loadFlows();
    setFlowId(newFlow.flow_id);
    await loadDraft(newFlow.flow_id);
    setFlows(list);
  };

  const doValidate = async () => {
    const res = await wrap(() => validateConfig(flowId));
    if (res) setValidation(res);
  };
  const doPreview = async () => {
    const res = await wrap(() => previewConfig(flowId));
    if (res) setPreview(res);
  };
  const doPublish = async () => {
    const res = await wrap(() => publishFlow(flowId, publishedVersion, publishNote), '发布成功，已刷新运行时注册表');
    if (!res) return;
    setPublishedVersion(Number(res.version || publishedVersion + 1));
    await loadFlows();
    await loadDraft(flowId);
    await refreshEntryPoints(true);
  };
  const loadVersions = async () => {
    const res = await wrap(() => listVersions(flowId));
    if (res) setVersions(res.versions || []);
  };
  const loadRollout = async () => {
    const res = await wrap(() => getRollout(flowId));
    if (res?.rollout) setRolloutState(res.rollout);
  };
  const loadHooks = async () => {
    const res = await wrap(() => listHooks());
    if (res) setHooks(res.hooks || []);
  };
  const loadCases = async () => {
    const res = await wrap(() => getTestCases(flowId));
    if (res) setCases(res.cases || []);
  };
  const loadAudit = async () => {
    const res = await wrap(() => getConfigAudit(flowId));
    if (res) setAudit(res.logs || []);
  };

  useEffect(() => {
    if (!flowId) return;
    loadVersions(); loadRollout(); loadHooks(); loadCases(); loadAudit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowId, publishedVersion]);

  const fields = useMemo(() => (Array.isArray(definition.fields) ? definition.fields : []), [definition.fields]);

  const updateField = (index: number, patch: Record<string, any>) => {
    const next = fields.map((f: any, i: number) => i === index ? { ...f, ...patch } : f);
    setDefinition((d) => ({ ...d, fields: next }));
    if (section === 'fields') setJsonText(JSON.stringify(next, null, 2));
  };

  const fieldColumns: ColumnsType<any> = [
    { title: 'key', dataIndex: 'key', width: 140, render: (v: string, _r: any, i: number) =>
      <Input size="small" value={v} onChange={(e) => updateField(i, { key: e.target.value })} /> },
    { title: '显示名', dataIndex: 'label', width: 150, render: (v: string, _r: any, i: number) =>
      <Input size="small" value={v} onChange={(e) => updateField(i, { label: e.target.value })} /> },
    { title: '分组', dataIndex: 'group', width: 120, render: (v: string, _r: any, i: number) =>
      <Input size="small" value={v || ''} onChange={(e) => updateField(i, { group: e.target.value })} /> },
    { title: '类型', dataIndex: 'type', width: 130, render: (v: string, _r: any, i: number) =>
      <Select size="small" style={{ width: 110 }} value={v || 'string'} options={FIELD_TYPES.map((t) => ({ value: t, label: t }))}
              onChange={(t) => updateField(i, { type: t })} /> },
    { title: '必填', dataIndex: 'required', width: 80, render: (v: boolean, _r: any, i: number) =>
      <Select size="small" style={{ width: 70 }} value={v === false ? 'false' : 'true'}
              options={[{ value: 'true', label: '是' }, { value: 'false', label: '否' }]}
              onChange={(t) => updateField(i, { required: t === 'true' })} /> },
    { title: '占位提示', dataIndex: 'placeholder', render: (v: string, _r: any, i: number) =>
      <Input size="small" value={v || ''} onChange={(e) => updateField(i, { placeholder: e.target.value })} /> },
    { title: '', key: 'op', width: 60, render: (_: any, __: any, i: number) =>
      <Button size="small" danger type="text" onClick={() => {
        const next = fields.filter((_f: any, n: number) => n !== i);
        setDefinition((d) => ({ ...d, fields: next }));
      }}>删除</Button> },
  ];

  if (!canEdit) {
    return <div style={styles.page}><Alert type="error" showIcon message="无权访问"
      description="流程配置后台仅 config_editor / config_publisher / admin 可见。" /></div>;
  }

  const validationChecks = validation?.checks || [];

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <div>
          <h2 style={styles.title}>流程配置后台</h2>
          <div style={styles.subtitle}>
            流程即数据：改配置不改代码 · 草稿乐观锁 · dry-run 八项校验 · 强制双人发布 · 改版自动灰度
          </div>
        </div>
        <Space wrap>
          <Button onClick={async () => { const r = await wrap(() => bootstrapFlows(), '初始化完成'); if (r) { await loadFlows(); await loadDraft(flowId || 'driver_entry'); } }}>初始化 / 迁移</Button>
          <Button onClick={loadFlows}>刷新</Button>
          <Button type="primary" onClick={() => setNewOpen(true)}>新建流程</Button>
        </Space>
      </div>

      <Row gutter={16}>
        <Col span={6}>
          <Card size="small" title="已配置流程" style={styles.card}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {flows.map((f) => (
                <div key={f.flow_id} style={{ ...styles.flowItem, ...(f.flow_id === flowId ? styles.flowItemActive : {}) }}
                     onClick={() => { setFlowId(f.flow_id); loadDraft(f.flow_id); }}>
                  <div style={{ fontSize: 13, color: '#E4EBF5' }}>{f.name || f.flow_id}</div>
                  <div style={{ fontSize: 11, color: '#5E6E88', fontFamily: 'monospace' }}>{f.flow_id}</div>
                  <Space size={4} style={{ marginTop: 4 }} wrap>
                    <Tag color={f.published_version ? 'success' : 'default'}>已发布 v{f.published_version || 0}</Tag>
                    <Tag>草稿 r{f.draft_revision}</Tag>
                  </Space>
                </div>
              ))}
              {!flows.length && <div style={{ fontSize: 12, color: '#5E6E88' }}>暂无流程，点右上「新建流程」</div>}
            </div>
          </Card>
        </Col>
        <Col span={18}>
          <Spin spinning={loading || busy}>
            <Card size="small" style={styles.card}
                  title={<Space><span>{definition?.meta?.name || flowId || '未选择流程'}</span>
                    <Tag color="blue">草稿 r{revision}</Tag><Tag>v{publishedVersion}</Tag>
                    {updatedBy && <Tag>最后编辑：{updatedBy}</Tag>}</Space>}
                  extra={<Button type="primary" onClick={doSaveDraft}>保存草稿</Button>}>
              <Tabs items={[
                {
                  key: 'edit', label: '配置编辑', children: (
                    <Row gutter={12}>
                      <Col span={6}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                          {SECTIONS.map((s) => (
                            <Button key={s.key} size="small" type={section === s.key ? 'primary' : 'text'}
                                    style={{ textAlign: 'left', justifyContent: 'flex-start' }}
                                    onClick={() => onSectionChange(s.key)}>{s.label}</Button>
                          ))}
                        </div>
                      </Col>
                      <Col span={18}>
                        <div style={{ fontSize: 12, color: '#94A3B8', marginBottom: 6 }}>
                          {SECTIONS.find((s) => s.key === section)?.hint}
                        </div>
                        <Input.TextArea value={jsonText} onChange={(e) => onJsonChange(e.target.value)}
                                        rows={20} spellCheck={false}
                                        style={{ fontFamily: 'monospace', fontSize: 12, background: '#0A1224', color: '#E4EBF5' }} />
                        {parseError
                          ? <Alert type="error" showIcon style={{ marginTop: 8 }} message="JSON 语法错误" description={parseError} />
                          : <div style={{ fontSize: 11, color: '#5E6E88', marginTop: 6 }}>
                              编辑即时生效于本地草稿，点右上「保存草稿」写入数据库（带乐观锁与 dry-run 自检）。
                              配置只能写数据，不能写表达式、脚本或任意代码。
                            </div>}
                      </Col>
                    </Row>
                  ),
                },
                {
                  key: 'fields', label: '字段向导', children: (
                    <>
                      <Space style={{ marginBottom: 10 }}>
                        <Button onClick={() => {
                          const next = [...fields, { key: `field_${fields.length + 1}`, label: '新字段',
                            group: definition.groups?.[0]?.key || 'base', type: 'string', required: true,
                            order: fields.length, source: 'user', placeholder: '' }];
                          setDefinition((d) => ({ ...d, fields: next }));
                        }}>添加字段</Button>
                        <span style={{ fontSize: 12, color: '#5E6E88' }}>改完记得点「保存草稿」</span>
                      </Space>
                      <Table rowKey={(_r, i) => String(i)} size="small" pagination={false} scroll={{ x: 900 }}
                             columns={fieldColumns} dataSource={fields} locale={{ emptyText: '暂无字段' }} />
                    </>
                  ),
                },
                {
                  key: 'validate', label: '校验与预览', children: (
                    <>
                      <Space style={{ marginBottom: 12 }} wrap>
                        <Button type="primary" onClick={doValidate}>运行 dry-run 八项校验</Button>
                        <Button onClick={doPreview}>预览确认卡 / 工具 / 菜单</Button>
                        <Button onClick={async () => { const r = await wrap(() => runTestCases(flowId)); if (r) setCaseResult(r); }}>运行意图用例</Button>
                      </Space>
                      {validation && (
                        <Alert type={validation.ok ? 'success' : 'error'} showIcon style={{ marginBottom: 12 }}
                               message={validation.ok ? '全部校验通过，可以提交发布' : `校验未通过（${(validation.errors || []).length} 项错误）`}
                               description={(validation.errors || []).join('；')} />
                      )}
                      {validation && (
                        <Table size="small" pagination={false} rowKey="id" dataSource={validationChecks}
                               columns={[
                                 { title: '#', dataIndex: 'id', width: 50 },
                                 { title: '校验项', dataIndex: 'name' },
                                 { title: '结果', dataIndex: 'ok', width: 90, render: (v: boolean) => <Tag color={v ? 'success' : 'error'}>{v ? 'OK' : '失败'}</Tag> },
                                 { title: '明细', dataIndex: 'details', render: (v: string[]) => (v || []).join('；') || '—' },
                               ]} />
                      )}
                      {validation?.warnings?.length > 0 && (
                        <Alert type="warning" showIcon style={{ marginTop: 12 }} message="警告（不阻断发布）"
                               description={validation.warnings.join('；')} />
                      )}
                      {caseResult && (
                        <Alert style={{ marginTop: 12 }} type={caseResult.failed?.length ? 'error' : 'success'}
                               message={`意图用例：${caseResult.passed}/${caseResult.total} 通过`}
                               description={JSON.stringify(caseResult.failed || [])} />
                      )}
                      {preview && (
                        <pre style={styles.pre}>{JSON.stringify(preview, null, 2)}</pre>
                      )}
                    </>
                  ),
                },
                {
                  key: 'publish', label: '发布与灰度', children: (
                    <>
                      <Alert type="info" showIcon style={{ marginBottom: 12 }}
                             message="强制双人发布（D12）"
                             description="草稿最后提交者与发布者必须是不同账号；发布前自动跑 dry-run 八项，任何一项失败都拒绝发布。" />
                      <Descriptions bordered size="small" column={2} style={{ marginBottom: 12 }}>
                        <Descriptions.Item label="当前发布版">v{publishedVersion}</Descriptions.Item>
                        <Descriptions.Item label="草稿最后编辑">{updatedBy || '—'}</Descriptions.Item>
                      </Descriptions>
                      <Space style={{ marginBottom: 16 }} wrap>
                        <Input placeholder="发布说明（可选）" value={publishNote}
                               onChange={(e) => setPublishNote(e.target.value)} style={{ width: 280 }} />
                        <Button type="primary" disabled={!canPublish} onClick={doPublish}>发布草稿</Button>
                        {!canPublish && <span style={{ fontSize: 12, color: '#F59E0B' }}>当前账号无发布权限（需 config_publisher / admin）</span>}
                      </Space>
                      <h4 style={styles.h4}>灰度设置（D13：改版必灰度，按用户组 / 账号白名单）</h4>
                      <Space wrap style={{ marginBottom: 10 }}>
                        <Select style={{ width: 120 }} value={rollout.mode || 'full'}
                                options={[{ value: 'gray', label: '灰度' }, { value: 'full', label: '全量' }]}
                                onChange={(v) => setRolloutState((r: any) => ({ ...r, mode: v }))} />
                        <InputNumber addonBefore="版本" min={0} value={rollout.version || 0}
                                     onChange={(v) => setRolloutState((r: any) => ({ ...r, version: Number(v || 0) }))} />
                        <InputNumber addonBefore="观察天数" min={1} value={rollout.observe_days || 1}
                                     onChange={(v) => setRolloutState((r: any) => ({ ...r, observe_days: Number(v || 1) }))} />
                        <Button disabled={!canPublish} onClick={async () => { await wrap(() => setRollout(flowId, rollout), '灰度配置已保存'); await loadRollout(); }}>保存灰度</Button>
                      </Space>
                      <Row gutter={12}>
                        <Col span={12}>
                          <div style={styles.label}>账号白名单（逗号分隔）</div>
                          <Input.TextArea rows={2} value={(rollout.users || []).join(',')}
                                          onChange={(e) => setRolloutState((r: any) => ({ ...r, users: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) }))} />
                        </Col>
                        <Col span={12}>
                          <div style={styles.label}>用户组白名单（逗号分隔）</div>
                          <Input.TextArea rows={2} value={(rollout.groups || []).join(',')}
                                          onChange={(e) => setRolloutState((r: any) => ({ ...r, groups: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) }))} />
                        </Col>
                      </Row>
                    </>
                  ),
                },
                {
                  key: 'versions', label: '版本与回滚', children: (
                    <>
                      <Space style={{ marginBottom: 12 }}>
                        <Button onClick={loadVersions}>刷新版本</Button>
                      </Space>
                      <Table size="small" rowKey="version" pagination={false} dataSource={versions}
                             columns={[
                               { title: '版本', dataIndex: 'version', width: 80 },
                               { title: '发布人', dataIndex: 'published_by', width: 130 },
                               { title: '提交人', dataIndex: 'submitted_by', width: 130 },
                               { title: '时间', dataIndex: 'published_at', width: 180, render: (v: string) => v?.slice(0, 19).replace('T', ' ') || '—' },
                               { title: '说明', dataIndex: 'note' },
                               { title: '操作', key: 'op', width: 170, render: (_: any, r: any) => (
                                 <Space size={4}>
                                   <Button size="small" onClick={async () => { const d = await wrap(() => getVersionDiff(flowId, r.version)); if (d) setDiff(d); }}>diff</Button>
                                   <Button size="small" danger disabled={!canPublish}
                                           onClick={async () => { const res = await wrap(() => rollbackFlow(flowId, r.version), '已回滚'); if (res) { await loadFlows(); await loadDraft(flowId); await refreshEntryPoints(true); } }}>回滚</Button>
                                 </Space>
                               ) },
                             ]} />
                      {diff && <pre style={styles.pre}>{JSON.stringify(diff, null, 2)}</pre>}
                    </>
                  ),
                },
                {
                  key: 'cases', label: '用例与扩展点', children: (
                    <>
                      <h4 style={styles.h4}>意图用例表（存在 DB，发布前自动回归）</h4>
                      <Table size="small" pagination={false} rowKey={(_r, i) => String(i)} dataSource={cases}
                             columns={[
                               { title: '问句', dataIndex: 'text', render: (v: string, _r: any, i: number) =>
                                 <Input size="small" value={v} onChange={(e) => setCases((cs) => cs.map((c, n) => n === i ? { ...c, text: e.target.value } : c))} /> },
                               { title: '期望流程', dataIndex: 'expect_flow_id', width: 170, render: (v: string, _r: any, i: number) =>
                                 <Input size="small" value={v} onChange={(e) => setCases((cs) => cs.map((c, n) => n === i ? { ...c, expect_flow_id: e.target.value } : c))} /> },
                               { title: '期望动作', dataIndex: 'expect_action', width: 140, render: (v: string, _r: any, i: number) =>
                                 <Select size="small" style={{ width: 120 }} value={v || 'action'}
                                         options={['action', 'readonly', 'choice', 'status', 'attach', 'none'].map((x) => ({ value: x, label: x }))}
                                         onChange={(x) => setCases((cs) => cs.map((c, n) => n === i ? { ...c, expect_action: x } : c))} /> },
                               { title: '', width: 60, key: 'op', render: (_: any, __: any, i: number) =>
                                 <Button size="small" danger type="text" onClick={() => setCases((cs) => cs.filter((_c, n) => n !== i))}>删除</Button> },
                             ]} />
                      <Space style={{ margin: '10px 0 18px' }} wrap>
                        <Button onClick={() => setCases((cs) => [...cs, { text: '', expect_flow_id: flowId, expect_action: 'action' }])}>添加用例</Button>
                        <Button disabled={!canEdit} onClick={async () => { const r = await wrap(() => saveTestCases(flowId, cases, revision), '用例已保存'); if (r) await loadDraft(flowId); }}>保存用例表</Button>
                        <Button onClick={async () => { const r = await wrap(() => runTestCases(flowId)); if (r) setCaseResult(r); }}>运行用例</Button>
                      </Space>
                      <h4 style={styles.h4}>命名扩展点白名单（D16 三态生命周期）</h4>
                      <Table size="small" pagination={false} rowKey="name" dataSource={hooks}
                             columns={[
                               { title: '扩展点', dataIndex: 'name' },
                               { title: '阶段', dataIndex: 'phase', width: 150 },
                               { title: '状态', dataIndex: 'lifecycle', width: 120, render: (v: string) =>
                                 <Tag color={v === 'active' ? 'success' : v === 'deprecated' ? 'warning' : 'default'}>{v}</Tag> },
                               { title: '说明', dataIndex: 'description' },
                             ]} />
                    </>
                  ),
                },
                {
                  key: 'audit', label: '变更审计', children: (
                    <>
                      <Button style={{ marginBottom: 10 }} onClick={loadAudit}>刷新审计</Button>
                      <Table size="small" rowKey={(_r, i) => String(i)} dataSource={audit} pagination={{ pageSize: 20 }}
                             columns={[
                               { title: '时间', dataIndex: 'created_at', width: 180, render: (v: string) => v?.slice(0, 19).replace('T', ' ') || '—' },
                               { title: '动作', dataIndex: 'action', width: 150 },
                               { title: '操作人', dataIndex: 'actor_username', width: 130 },
                               { title: '详情', dataIndex: 'detail', render: (v: any) => <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{JSON.stringify(v)}</span> },
                             ]} />
                    </>
                  ),
                },
              ]} />
            </Card>
          </Spin>
        </Col>
      </Row>

      <Modal title="新建流程（不改代码）" open={newOpen} onCancel={() => setNewOpen(false)} onOk={doCreate} okText="创建草稿">
        <Form layout="vertical">
          <Form.Item label="流程标识 flow_id（小写字母/数字/下划线，创建后不可改）" required>
            <Input value={newFlow.flow_id} placeholder="如 warehouse_inbound"
                   onChange={(e) => setNewFlow((s) => ({ ...s, flow_id: e.target.value }))} />
          </Form.Item>
          <Form.Item label="流程名称" required>
            <Input value={newFlow.name} placeholder="如 危化品入库申请"
                   onChange={(e) => setNewFlow((s) => ({ ...s, name: e.target.value }))} />
          </Form.Item>
          <Form.Item label="意图场景词（用于区分其他流程，留空则用流程名）">
            <Input value={newFlow.keyword} placeholder="如 危化品入库"
                   onChange={(e) => setNewFlow((s) => ({ ...s, keyword: e.target.value }))} />
          </Form.Item>
          <Alert type="info" showIcon message="系统会生成一份已含字段、意图规则、审批链、菜单入口的最小配置，可直接通过 dry-run；之后在编辑器里改文字和字段即可。" />
        </Form>
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
  flowItem: { padding: 10, borderRadius: 8, cursor: 'pointer', border: '1px solid #1A2748', background: '#0A1224' },
  flowItemActive: { borderColor: '#4F8CFF', background: 'rgba(79,140,255,0.10)' },
  pre: { marginTop: 12, maxHeight: 320, overflow: 'auto', padding: 12, background: '#0A1224', border: '1px solid #1A2748', borderRadius: 8, color: '#94A3B8', fontSize: 11 },
  h4: { color: '#94A3B8', fontSize: 13, margin: '16px 0 10px' },
  label: { color: '#5E6E88', fontSize: 11, marginBottom: 4 },
};

export default FlowConfigPage;
