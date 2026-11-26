import React, { useEffect, useState } from 'react';
import { Alert, Descriptions, Divider, Empty, Spin, Tag, Timeline, Typography } from 'antd';
import type { FlowApplication, FlowRenderConfig } from '../../api/flow';
import { SOURCE_LABELS, STATUS_COLORS, fetchFlowAttachmentBlobUrl, getFlowAudit, getFlowPass } from '../../api/flow';

const { Text } = Typography;

interface Props {
  application: FlowApplication;
  config: FlowRenderConfig;
  showAudit?: boolean;
}

/** 通用申请单详情：分组、字段、附件类型、状态显示名全部取自后端 render-config。 */
const FlowApplicationDetail: React.FC<Props> = ({ application, config, showAudit = true }) => {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [pass, setPass] = useState<any | null>(null);
  const flowId = config.flow_id;
  const labels = config.state_labels || {};
  const kinds = config.attachments || [];
  const groups = config.groups || [];
  const fields = config.fields || [];

  useEffect(() => {
    let cancelled = false;
    const objectUrls: string[] = [];
    (async () => {
      setLoading(true);
      const attachments = application.form_data?.attachments || {};
      const next: Record<string, string> = {};
      await Promise.all(kinds.map(async (k) => {
        const id = attachments[k.key];
        if (!id) return;
        try {
          const url = await fetchFlowAttachmentBlobUrl(flowId, id);
          objectUrls.push(url);
          next[k.key] = url;
        } catch { next[k.key] = ''; }
      }));
      if (!cancelled) setUrls(next);
      if (showAudit) {
        try {
          const res = await getFlowAudit(flowId, application.id);
          if (!cancelled) setLogs(res.logs || []);
        } catch { if (!cancelled) setLogs([]); }
      }
      if (application.status === 'approved') {
        try { if (!cancelled) setPass(await getFlowPass(flowId, application.id)); }
        catch { if (!cancelled) setPass(null); }
      }
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
      objectUrls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [application.id, application.status, showAudit, flowId, kinds]);

  const form = application.form_data || {};

  return (
    <Spin spinning={loading}>
      <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="申请单号">{application.id}</Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={STATUS_COLORS[application.status]}>{labels[application.status] || application.status}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="来源">
          {application.assisted_by_ai ? SOURCE_LABELS.assisted : SOURCE_LABELS.manual}
        </Descriptions.Item>
        <Descriptions.Item label="申请人账号">{application.applicant_username}</Descriptions.Item>
        <Descriptions.Item label="配置版本">v{application.definition_version}</Descriptions.Item>
        <Descriptions.Item label="创建时间">{application.created_at?.slice(0, 19).replace('T', ' ')}</Descriptions.Item>
        <Descriptions.Item label="确认提交时间">
          {application.confirmed_at ? application.confirmed_at.slice(0, 19).replace('T', ' ') : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="审核时间">
          {application.reviewed_at ? application.reviewed_at.slice(0, 19).replace('T', ' ') : '—'}
        </Descriptions.Item>
      </Descriptions>

      {groups.map((g) => {
        const items = fields.filter((f) => (f.group || 'default') === g.key);
        if (!items.length) return null;
        return (
          <React.Fragment key={g.key}>
            <Divider orientation="left" plain style={{ margin: '12px 0' }}>{g.label}</Divider>
            <Descriptions bordered size="small" column={2}>
              {items.map((f) => (
                <Descriptions.Item key={f.key} label={f.label}>
                  {form[f.key] === '' || form[f.key] == null
                    ? <Text type="secondary">未填写</Text>
                    : String(form[f.key])}
                </Descriptions.Item>
              ))}
            </Descriptions>
          </React.Fragment>
        );
      })}

      {kinds.length > 0 && (
        <>
          <Divider orientation="left" plain style={{ margin: '16px 0 12px' }}>照片与证件材料</Divider>
          {kinds.every((k) => !urls[k.key]) ? (
            <Empty description="暂无附件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
              {kinds.map((k) => (
                <div key={k.key} style={styles.attCard}>
                  {urls[k.key] ? (
                    <a href={urls[k.key]} target="_blank" rel="noreferrer">
                      <img src={urls[k.key]} alt={k.label} style={styles.attImg} />
                    </a>
                  ) : <div style={styles.attMissing}>未上传</div>}
                  <div style={styles.attLabel}>{k.label}</div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {application.status === 'rejected' && application.review_comment && (
        <Alert type="error" showIcon message="驳回原因" description={application.review_comment} style={{ marginTop: 16 }} />
      )}
      {application.status === 'approved' && (
        <Alert type="success" showIcon message={`审核通过（${application.reviewed_by || 'admin'}）`}
               description={application.review_comment || undefined} style={{ marginTop: 16 }} />
      )}
      {pass?.pass_no && (
        <div style={{ ...styles.passBox, marginTop: 12 }}>
          <div style={{ fontWeight: 600, fontFamily: 'monospace', marginBottom: 8 }}>
            编号 {pass.pass_no}
          </div>
          {pass.qr_svg && (
            <img src={pass.qr_svg} alt="二维码" style={{ width: 150, height: 150, background: '#FFFFFF', borderRadius: 6 }} />
          )}
        </div>
      )}

      {showAudit && logs.length > 0 && (
        <>
          <Divider orientation="left" plain style={{ margin: '16px 0 12px' }}>流转记录</Divider>
          <Timeline items={logs.map((l) => ({
            children: (
              <span style={{ fontSize: 12 }}>
                {l.created_at?.slice(0, 19).replace('T', ' ')} · {l.action} · {l.actor_username}
                {l.from_status ? ` · ${l.from_status} → ${l.to_status}` : ''}
              </span>
            ),
          }))} />
        </>
      )}
    </Spin>
  );
};

const styles: Record<string, React.CSSProperties> = {
  attCard: { width: 132, border: '1px solid #1A2748', borderRadius: 8, overflow: 'hidden', background: '#0D1525' },
  attImg: { width: '100%', height: 88, objectFit: 'cover', display: 'block' },
  attMissing: { width: '100%', height: 88, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#5E6E88', fontSize: 12 },
  attLabel: { padding: '6px 8px', fontSize: 12, color: '#94A3B8', textAlign: 'center' },
  passBox: { display: 'flex', flexDirection: 'column' as const, alignItems: 'center', padding: 12, border: '1px solid #1A2748', borderRadius: 8, background: '#0A1224', color: '#E4EBF5' },
};

export default FlowApplicationDetail;
