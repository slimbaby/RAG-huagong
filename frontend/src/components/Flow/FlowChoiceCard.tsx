import React, { useEffect, useState } from 'react';
import { Button } from 'antd';
import { QuestionCircleOutlined } from '@ant-design/icons';
import type { IntentCandidate, IntentChoice } from '../../types';

interface Props {
  choice: IntentChoice;
  onChoose: (candidate: IntentCandidate | null) => void;
}

/**
 * 通用两可 / 多流程候选卡。
 * 候选完全来自后端 intent_choice.candidates，前端没有任何关键词或判定规则。
 */
const FlowChoiceCard: React.FC<Props> = ({ choice, onChoose }) => {
  const candidates = choice.candidates || [];
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 16 }}>
      <div style={{
        width: 32, height: 32, borderRadius: 8,
        background: 'linear-gradient(-90deg, #3B6FE8 0%, #5B8CF6 100%)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        marginRight: 10, flexShrink: 0,
      }}>
        <QuestionCircleOutlined style={{ fontSize: 14, color: '#FFFFFF' }} />
      </div>
      <div style={{
        maxWidth: '70%', padding: '14px 16px', borderRadius: '12px 12px 12px 0',
        background: '#121D36', border: '1px solid #1A2748',
      }}>
        <div style={{ fontSize: 14, color: '#E4EBF5', marginBottom: 6 }}>
          {choice.hint || '请确认您要办理哪个流程'}
        </div>
        <div style={{ fontSize: 12, color: '#94A3B8', marginBottom: 12, wordBreak: 'break-all' }}>
          “{choice.question}”
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {candidates.map((c, i) => (
            <Button key={`${c.flow_id}_${i}`} type={i === 0 ? 'primary' : 'default'} size="small"
                    onClick={() => onChoose(c)}>
              帮我办理{c.name || c.flow_id}
            </Button>
          ))}
          <Button size="small" onClick={() => onChoose(null)}>我只是想咨询</Button>
        </div>
        <div style={{ fontSize: 11, color: '#5E6E88', marginTop: 10, lineHeight: 1.6 }}>
          选「办理」后，AI 只帮你整理信息并出确认卡；提交仍由你本人在表单里确认，AI 不写库。
        </div>
      </div>
    </div>
  );
};

export default FlowChoiceCard;
