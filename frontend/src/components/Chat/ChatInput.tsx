import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Dropdown, Input, Modal, Tag, Tooltip, message } from 'antd';
import { SendOutlined, ClearOutlined, PaperClipOutlined, RobotOutlined, LogoutOutlined } from '@ant-design/icons';
import { useChatStore } from '../../stores/chatStore';
import { useFlowStore } from '../../stores/flowStore';
import { uploadFlowAttachment } from '../../api/flow';
import type { FlowEntryPoint } from '../../api/flow';

const { TextArea } = Input;

/**
 * 对话输入框（通用）。
 * 快捷入口、附件类型、流程名称全部来自后端配置；这里没有任何流程关键词或判定规则。
 */
const ChatInput: React.FC = () => {
  const [question, setQuestion] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [shortcutFlowId, setShortcutFlowId] = useState<string>('');
  const fileRef = useRef<HTMLInputElement>(null);
  const pendingKind = useRef<string>('');

  const {
    sendMessage, isLoading, clearMessages, startFlowSession,
    setFlowAttachment, flowSlots, flowPayload, activeFlowId, clearFlowState,
  } = useChatStore();
  const {
    entryPoints, configs, loadEntryPoints, loadConfig, shortcutItems,
  } = useFlowStore();

  useEffect(() => { loadEntryPoints(); }, [loadEntryPoints]);

  // 快捷入口对应的渲染配置：聊天窗口上传附件时需要知道该流程有哪些材料类型
  useEffect(() => {
    const ids = Array.from(new Set(entryPoints.map((e) => e.flow_id)));
    ids.forEach((id) => { loadConfig(id); });
  }, [entryPoints, loadConfig]);

  useEffect(() => {
    if (activeFlowId) loadConfig(activeFlowId);
  }, [activeFlowId, loadConfig]);

  const shortcuts = useMemo(() => shortcutItems(), [entryPoints, shortcutItems]);
  const uploadFlowId = activeFlowId || shortcutFlowId || entryPoints[0]?.flow_id || '';
  const kindList = configs[uploadFlowId]?.attachments || [];
  const activeConfig = activeFlowId ? configs[activeFlowId] : undefined;
  const flowName = activeConfig?.name || entryPoints.find((e) => e.flow_id === activeFlowId)?.flow_name || '当前流程';

  const attachments = (flowSlots.attachments || {}) as Record<string, string>;
  const collectedCount = kindList.filter((k) => attachments[k.key]).length;
  const missing = flowPayload?.missing || [];
  const inFlow = Boolean(activeFlowId) || Object.keys(flowSlots).length > 0;

  const handleSend = async () => {
    const trimmed = question.trim();
    if (!trimmed || isLoading) return;
    setQuestion('');
    const opts = shortcutFlowId
      ? { flowId: shortcutFlowId, force: true, intentResolved: true }
      : undefined;
    setShortcutFlowId('');
    await sendMessage(trimmed, opts);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const startAiFlow = (ep: FlowEntryPoint) => {
    startFlowSession(ep.flow_id, `AI 帮我办理 · ${ep.flow_name}`);
    setShortcutFlowId(ep.flow_id);
    setQuestion(`帮我办理${ep.flow_name}`);
    message.info(`已进入「${ep.flow_name}」AI 辅助办理，补充信息或直接发送即可`);
  };

  const chooseKind = (kind: string) => {
    pendingKind.current = kind;
    setPickerOpen(false);
    fileRef.current?.click();
  };

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    const kind = pendingKind.current;
    if (!file || !kind || !uploadFlowId) return;
    setUploading(true);
    try {
      const res = await uploadFlowAttachment(uploadFlowId, file, kind);
      // 附件 ID 由前端显式写入状态，AI 只搬运、不猜测
      setFlowAttachment(uploadFlowId, kind, res.id);
      message.success(`${res.label || kind} 已上传，会随下一轮请求一起回传`);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '上传失败，请检查格式与大小');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={styles.wrapper}>
      {inFlow && (
        <div style={styles.flowBar}>
          <Tag color="blue">AI 辅助 · {flowName}</Tag>
          <span style={{ fontSize: 12, color: '#94A3B8' }}>
            已收集 {Object.keys(flowSlots).length} 项字段 · 附件 {collectedCount}/{kindList.length}
          </span>
          {missing.length > 0 && (
            <Tooltip title={missing.join('、')}>
              <span style={{ fontSize: 12, color: '#F59E0B' }}>
                还缺 {missing.length} 项：{missing.slice(0, 3).join('、')}{missing.length > 3 ? '…' : ''}
              </span>
            </Tooltip>
          )}
          <span style={{ fontSize: 11, color: '#5E6E88' }}>AI 不写库，提交由你在表单里确认</span>
          <Button type="text" size="small" icon={<LogoutOutlined />} onClick={clearFlowState}
                  style={{ marginLeft: 'auto', color: '#5E6E88', fontSize: 11 }}>
            退出流程
          </Button>
        </div>
      )}

      <div style={styles.inputRow}>
        <div style={styles.inputContainer}>
          <TextArea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={shortcuts.length
              ? `输入您的问题，或点 AI 按钮开始办理「${shortcuts[0].flow_name}」... (Enter发送，Shift+Enter换行)`
              : '输入您的问题... (Enter发送，Shift+Enter换行)'}
            autoSize={{ minRows: 1, maxRows: 4 }}
            disabled={isLoading}
            style={styles.textarea}
            bordered={false}
          />
        </div>
        <div style={styles.actions}>
          {shortcuts.length === 1 && (
            <Tooltip title={`AI 帮我办理${shortcuts[0].flow_name}`}>
              <Button icon={<RobotOutlined />} onClick={() => startAiFlow(shortcuts[0])} style={styles.clearBtn} />
            </Tooltip>
          )}
          {shortcuts.length > 1 && (
            <Dropdown
              menu={{
                items: shortcuts.map((ep, i) => ({ key: `${ep.flow_id}_${i}`, label: ep.label })),
                onClick: ({ key }) => {
                  const idx = Number(key.split('_').pop());
                  const ep = shortcuts[idx];
                  if (ep) startAiFlow(ep);
                },
              }}
              trigger={['click']}
            >
              <Button icon={<RobotOutlined />} style={styles.clearBtn} />
            </Dropdown>
          )}
          <Tooltip title="上传照片 / 证件材料">
            <Button icon={<PaperClipOutlined />} onClick={() => setPickerOpen(true)} style={styles.clearBtn} />
          </Tooltip>
          <Tooltip title="清空对话">
            <Button icon={<ClearOutlined />} onClick={clearMessages} style={styles.clearBtn} />
          </Tooltip>
          <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={isLoading} disabled={!question.trim()} style={styles.sendBtn}>
            发送
          </Button>
        </div>
      </div>

      <input
        ref={fileRef}
        type="file"
        accept=".jpg,.jpeg,.png,.webp,.pdf"
        style={{ display: 'none' }}
        onChange={handleFile}
      />

      <Modal
        title="选择要上传的材料类型"
        open={pickerOpen}
        onCancel={() => setPickerOpen(false)}
        footer={null}
        width={440}
      >
        <div style={styles.kindGrid}>
          {kindList.length === 0 && <div style={{ fontSize: 12, color: '#5E6E88' }}>当前流程未配置附件类型</div>}
          {kindList.map((k) => (
            <Button key={k.key} block disabled={uploading || !uploadFlowId} onClick={() => chooseKind(k.key)}>
              {k.label}{attachments[k.key] ? '（已上传）' : ''}
            </Button>
          ))}
        </div>
        <div style={{ fontSize: 12, color: '#5E6E88', marginTop: 12 }}>
          附件独立存储，不会进入知识库检索。上传后请在对话里补充其余信息。
        </div>
      </Modal>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  wrapper: { width: '100%' },
  flowBar: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8, padding: '6px 10px', border: '1px solid #1A2748', borderRadius: 8, background: '#0A1224' },
  kindGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 },
  inputRow: { display: 'flex', alignItems: 'flex-end', gap: 12 },
  inputContainer: { flex: 1, background: 'linear-gradient(180deg, #0A1224 0%, #0F192D 100%)', border: '1px solid #2A4080', borderRadius: 12, padding: '2px 0', transition: 'border-color 0.2s' },
  textarea: { background: 'transparent', color: '#E4EBF5', fontSize: 13, fontFamily: "'Inter', sans-serif", resize: 'none' as const, padding: '10px 14px' },
  actions: { display: 'flex', gap: 8, flexShrink: 0 },
  clearBtn: { width: 40, height: 40, borderRadius: 8, background: '#0D1525', border: '1px solid #1A2748', color: '#5E6E88', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  sendBtn: { height: 40, borderRadius: 8, background: 'linear-gradient(-90deg, #3B6FE8 0%, #6366F1 100%)', border: 'none', color: '#FFFFFF', fontWeight: 600, fontFamily: "'Inter', sans-serif", display: 'flex', alignItems: 'center', gap: 6 },
};

export default ChatInput;
