import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Space, Tag, message } from 'antd';
import { CheckCircleOutlined, EditOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { FlowPayload } from '../../types';
import { fetchFlowAttachmentBlobUrl } from '../../api/flow';
import { prefillKey, useFlowStore } from '../../stores/flowStore';

interface Props {
  payload: FlowPayload;
}

/**
 * 配置驱动的通用确认卡：标题、分组、字段、附件全部来自后端 build_card。
 * 只展示与跳转，绝不在这里写库 / 提交。
 */
const FlowConfirmCard: React.FC<Props> = ({ payload }) => {
  const navigate = useNavigate();
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [target, setTarget] = useState<string>('');
  const card = payload.card;
  const flowId = payload.flow_id || '';
  const config = useFlowStore((s) => (flowId ? s.configs[flowId] : undefined));
  const loadConfig = useFlowStore((s) => s.loadConfig);

  useEffect(() => {
    if (!flowId) return;
    loadConfig(flowId).then((cfg) => {
      if (cfg) setTarget(cfg.card?.prefill_target || `/flows/${flowId}/apply`);
    });
  }, [flowId, loadConfig]);

  useEffect(() => {
    let cancelled = false;
    const urls: string[] = [];
    (async () => {
      const next: Record<string, string> = {};
      await Promise.all((card?.attachments || []).map(async (a) => {
        if (!a.id || !flowId) return;
        try {
          const url = await fetchFlowAttachmentBlobUrl(flowId, a.id);
          urls.push(url);
          next[a.key] = url;
        } catch { /* 附件不可用则显示占位 */ }
      }));
      if (!cancelled) setThumbs(next);
    })();
    return () => {
      cancelled = true;
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [card, flowId]);

  const goForm = () => {
    // 完整字段只在用户浏览器内存里流转，不写进会话快照
    if (!flowId) return;
    sessionStorage.setItem(prefillKey(flowId), JSON.stringify(payload.slots || {}));
    navigate(`${target || `/flows/${flowId}/apply`}?prefill=1`);
  };

  if (!card) return null;

  return (
    <Card
      size="small"
      title={<span><CheckCircleOutlined style={{ color: '#34D399', marginRight: 8 }} />{card.title}</span>}
      style={{ marginTop: 10, background: '#0F192D', border: '1px solid #2A4080' }}
      headStyle={{ color: '#E4EBF5', borderBottom: '1px solid #1A2748' }}
      bodyStyle={{ padding: 12 }}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="请逐项核对，敏感证件字段已打码"
        description="AI 只帮你把表填好，不会代你提交；确认后由你在申请表单里点「确认提交」，管理员才会看到。"
      />

      {(card.groups || []).map((g) => (
        <div key={g.key} style={{ marginBottom: 10 }}>
          <div style={styles.groupTitle}>{g.label}</div>
          <div style={styles.grid}>
            {(g.items || []).map((it) => (
              <div key={it.key} style={styles.item}>
                <span style={styles.label}>{it.label}</span>
                <span style={{ ...styles.value, color: it.filled ? '#E4EBF5' : '#EF4444' }}>
                  {it.filled ? it.value : '未填写'}
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}

      {(card.attachments || []).length > 0 && (
        <>
          <div style={styles.groupTitle}>照片与证件</div>
          <div style={styles.attGrid}>
            {card.attachments.map((a) => (
              <div key={a.key} style={styles.attCard}>
                {thumbs[a.key] ? (
                  <a href={thumbs[a.key]} target="_blank" rel="noreferrer">
                    <img src={thumbs[a.key]} alt={a.label} style={styles.thumb} />
                  </a>
                ) : (
                  <div style={styles.thumbMissing}>{a.filled ? '预览失败' : '未上传'}</div>
                )}
                <div style={styles.attLabel}>{a.label}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {(payload.errors || []).length > 0 && (
        <Alert type="error" showIcon style={{ marginTop: 12 }}
               message="需要修正" description={payload.errors.join('；')} />
      )}

      <Space style={{ marginTop: 14 }} wrap>
        <Button type="primary" icon={<EditOutlined />} onClick={goForm} disabled={!flowId}>
          去表单核对并提交
        </Button>
        <Button onClick={() => message.info('直接在对话框里补充或修改即可')}>
          继续补充信息
        </Button>
        <Tag color="blue">{config?.name || flowId} · 已收集 {Object.keys(payload.slots || {}).length} 项</Tag>
      </Space>
    </Card>
  );
};

const styles: Record<string, React.CSSProperties> = {
  groupTitle: { fontSize: 12, color: '#94A3B8', margin: '6px 0', fontWeight: 600 },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 6 },
  item: { display: 'flex', flexDirection: 'column', gap: 2, padding: '6px 8px', background: '#0A1224', border: '1px solid #1A2748', borderRadius: 6 },
  label: { fontSize: 11, color: '#5E6E88' },
  value: { fontSize: 12, fontFamily: 'monospace', wordBreak: 'break-all' },
  attGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(88px, 1fr))', gap: 8 },
  attCard: { border: '1px solid #1A2748', borderRadius: 6, overflow: 'hidden', background: '#0A1224' },
  thumb: { width: '100%', height: 62, objectFit: 'cover', display: 'block' },
  thumbMissing: { width: '100%', height: 62, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: '#5E6E88' },
  attLabel: { padding: '4px 6px', fontSize: 11, color: '#94A3B8', textAlign: 'center' },
};

export default FlowConfirmCard;
