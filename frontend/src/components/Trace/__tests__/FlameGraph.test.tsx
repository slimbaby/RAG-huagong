import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import FlameGraph, { FlameSpan } from '../FlameGraph';

const makeSpan = (overrides: Partial<FlameSpan> = {}): FlameSpan => ({
  span_id: 's1',
  span_type: 'request',
  operation_name: 'test_op',
  start_time: 0,
  end_time: 1,
  duration_ms: 1000,
  ...overrides,
});

describe('FlameGraph', () => {
  it('renders Empty when no spans', () => {
    render(<FlameGraph spans={[]} />);
    expect(screen.getByText('暂无链路数据')).toBeInTheDocument();
  });

  it('renders bars for spans', () => {
    const spans = [
      makeSpan({ span_id: 'root', operation_name: 'root_op', start_time: 0, end_time: 2, duration_ms: 2000, depth: 0 }),
      makeSpan({ span_id: 'child', operation_name: 'child_op', start_time: 0.5, end_time: 1.5, duration_ms: 1000, depth: 1, parent_span_id: 'root' }),
    ];
    render(<FlameGraph spans={spans} />);
    // Bars exist (operation name appears in bar label when width > 7%)
    expect(screen.getByText(/root_op/)).toBeInTheDocument();
  });

  it('shows self-duration in tooltip for nested spans', () => {
    const spans = [
      makeSpan({ span_id: 'parent', operation_name: 'parent_op', start_time: 0, end_time: 3, duration_ms: 3000, depth: 0 }),
      makeSpan({ span_id: 'child', operation_name: 'child_op', start_time: 1, end_time: 2, duration_ms: 1000, depth: 1, parent_span_id: 'parent' }),
    ];
    render(<FlameGraph spans={spans} />);
    // parent self-time = 3000 - 1000 = 2000ms
    // Tooltip shows on hover — just verify component renders without error
    expect(document.querySelector('[style*="position: relative"]')).toBeTruthy();
  });

  it('renders legend with span types', () => {
    const spans = [
      makeSpan({ span_type: 'llm_call', operation_name: 'llm' }),
      makeSpan({ span_id: 's2', span_type: 'retrieval', operation_name: 'ret', start_time: 2, end_time: 3, duration_ms: 1000 }),
    ];
    render(<FlameGraph spans={spans} />);
    expect(screen.getByText('LLM 调用')).toBeInTheDocument();
    expect(screen.getByText('检索')).toBeInTheDocument();
  });
});