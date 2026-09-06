import React from 'react';
import { Empty, Tooltip } from 'antd';

export interface FlameSpan {
  span_id: string;
  trace_id?: string;
  parent_span_id?: string | null;
  span_type: string;
  operation_name: string;
  start_time: number;
  end_time?: number;
  duration_ms?: number;
  depth?: number;
  status?: string;
  error_message?: string;
  inputs?: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

interface FlameGraphProps {
  spans: FlameSpan[];
  /** trace 级总耗时，仅用于展示，条宽按 span 自身时间范围计算 */
  totalDurationMs?: number;
  onSelect?: (span: FlameSpan) => void;
}

const TYPE_COLORS: Record<string, string> = {
  request: '#4F8CFF',
  query_optimize: '#A78BFA',
  retrieval: '#34D399',
  vector_search: '#22D3EE',
  bm25_search: '#FBBF24',
  hybrid_fusion: '#F472B6',
  llm_call: '#FB923C',
  tool_call: '#94A3B8',
  checkpoint: '#64748B',
};

const TYPE_LABELS: Record<string, string> = {
  request: '请求',
  query_optimize: '查询优化',
  retrieval: '检索',
  vector_search: '向量检索',
  bm25_search: 'BM25',
  hybrid_fusion: '结果融合',
  llm_call: 'LLM 调用',
  tool_call: '工具调用',
  checkpoint: '检查点',
};

const ROW_HEIGHT = 24;
const ROW_GAP = 3;

const colorOf = (span: FlameSpan) =>
  span.status === 'error' ? '#EF4444' : TYPE_COLORS[span.span_type] || '#64748B';

const formatMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`);

/**
 * 计算每个 span 的层级。新数据自带 depth；老数据 parent_span_id 全为 null，
 * 从父子关系推断，推断不出来则退化为第 0 层。
 */
function computeDepths(spans: FlameSpan[]): Map<string, number> {
  const byId = new Map(spans.map((s) => [s.span_id, s]));
  const depths = new Map<string, number>();

  const resolve = (span: FlameSpan, guard: Set<string>): number => {
    if (depths.has(span.span_id)) return depths.get(span.span_id)!;
    let depth: number;
    if (typeof span.depth === 'number') {
      depth = span.depth;
    } else if (!span.parent_span_id || !byId.has(span.parent_span_id) || guard.has(span.span_id)) {
      depth = 0;
    } else {
      guard.add(span.span_id);
      depth = resolve(byId.get(span.parent_span_id)!, guard) + 1;
    }
    depths.set(span.span_id, depth);
    return depth;
  };

  spans.forEach((s) => resolve(s, new Set()));
  return depths;
}

/** 自身耗时 = 总耗时 - 直接子 span 耗时之和，用于区分"自己慢"还是"下游慢" */
function computeSelfTimes(spans: FlameSpan[]): Map<string, number> {
  const durationOf = (s: FlameSpan) =>
    s.duration_ms ?? Math.max(0, ((s.end_time ?? s.start_time) - s.start_time) * 1000);
  const childCost = new Map<string, number>();
  spans.forEach((s) => {
    if (!s.parent_span_id) return;
    childCost.set(s.parent_span_id, (childCost.get(s.parent_span_id) || 0) + durationOf(s));
  });
  const self = new Map<string, number>();
  spans.forEach((s) => self.set(s.span_id, Math.max(0, durationOf(s) - (childCost.get(s.span_id) || 0))));
  return self;
}

const FlameGraph: React.FC<FlameGraphProps> = ({ spans, totalDurationMs, onSelect }) => {
  const [hoveredId, setHoveredId] = React.useState<string | null>(null);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);

  if (!spans || spans.length === 0) {
    return <Empty description="暂无链路数据" style={{ padding: 24 }} />;
  }

  const durationOf = (s: FlameSpan) =>
    s.duration_ms ?? Math.max(0, ((s.end_time ?? s.start_time) - s.start_time) * 1000);

  const t0 = Math.min(...spans.map((s) => s.start_time));
  const t1 = Math.max(...spans.map((s) => s.end_time ?? s.start_time + durationOf(s) / 1000));
  // start_time/end_time 是秒，duration_ms 是毫秒，位置按秒算、宽度按毫秒算
  const rangeSec = Math.max(t1 - t0, 0.001);
  const spanRangeMs = rangeSec * 1000;
  const totalMs = totalDurationMs && totalDurationMs > 0 ? totalDurationMs : spanRangeMs;

  const depths = computeDepths(spans);
  const selfTimes = computeSelfTimes(spans);
  const maxDepth = Math.max(...Array.from(depths.values()));

  const ordered = [...spans].sort((a, b) => {
    const da = depths.get(a.span_id) ?? 0;
    const db = depths.get(b.span_id) ?? 0;
    return da !== db ? da - db : a.start_time - b.start_time;
  });

  const rows: FlameSpan[][] = Array.from({ length: maxDepth + 1 }, () => []);
  ordered.forEach((s) => rows[depths.get(s.span_id) ?? 0].push(s));

  const selected = spans.find((s) => s.span_id === selectedId) || null;
  const types = Array.from(new Set(spans.map((s) => s.span_type)));

  const handleClick = (span: FlameSpan) => {
    setSelectedId(span.span_id === selectedId ? null : span.span_id);
    onSelect?.(span);
  };

  return (
    <div style={{ background: '#0A1224', border: '1px solid #1A2748', borderRadius: 8, padding: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', color: '#5E6E88', fontSize: 11, marginBottom: 6, fontFamily: 'monospace' }}>
        {[0, 0.25, 0.5, 0.75, 1].map((r) => (
          <span key={r}>{formatMs(spanRangeMs * r)}</span>
        ))}
      </div>

      <div style={{ position: 'relative' }}>
        {[0, 0.25, 0.5, 0.75, 1].map((r) => (
          <div key={r} style={{ position: 'absolute', left: `${r * 100}%`, top: 0, bottom: 0, width: 1, background: 'rgba(26, 39, 72, 0.9)' }} />
        ))}
        {rows.map((row, depth) => (
          <div key={depth} style={{ position: 'relative', height: ROW_HEIGHT, marginBottom: ROW_GAP }}>
            {row.map((span) => {
              const dur = durationOf(span);
              const left = ((span.start_time - t0) / rangeSec) * 100;
              const width = Math.max((dur / spanRangeMs) * 100, 0.4);
              const selfMs = selfTimes.get(span.span_id) ?? dur;
              const isActive = hoveredId === span.span_id || selectedId === span.span_id;
              return (
                <Tooltip
                  key={span.span_id}
                  title={
                    <div style={{ fontSize: 12 }}>
                      <div style={{ fontWeight: 500 }}>{span.operation_name}</div>
                      <div>类型：{TYPE_LABELS[span.span_type] || span.span_type}</div>
                      <div>耗时：{formatMs(dur)}（占总链路 {((dur / totalMs) * 100).toFixed(1)}%）</div>
                      <div>自身耗时：{formatMs(selfMs)}（{(dur > 0 ? (selfMs / dur) * 100 : 0).toFixed(0)}%）</div>
                      <div>层级：{depths.get(span.span_id) ?? 0}</div>
                      {span.error_message && <div style={{ color: '#FCA5A5' }}>错误：{span.error_message}</div>}
                    </div>
                  }
                >
                  <div
                    onClick={() => handleClick(span)}
                    onMouseEnter={() => setHoveredId(span.span_id)}
                    onMouseLeave={() => setHoveredId(null)}
                    style={{
                      position: 'absolute',
                      left: `${left}%`,
                      width: `${width}%`,
                      minWidth: 3,
                      height: ROW_HEIGHT,
                      background: colorOf(span),
                      opacity: isActive ? 1 : 0.82,
                      borderRadius: 3,
                      cursor: 'pointer',
                      overflow: 'hidden',
                      whiteSpace: 'nowrap',
                      textOverflow: 'ellipsis',
                      paddingLeft: 6,
                      paddingRight: 4,
                      lineHeight: `${ROW_HEIGHT}px`,
                      fontSize: 11,
                      color: '#0A1224',
                      fontWeight: 500,
                      boxSizing: 'border-box',
                      outline: selectedId === span.span_id ? '2px solid #E4EBF5' : 'none',
                      transition: 'opacity 0.15s',
                    }}
                  >
                    {width > 7 ? `${span.operation_name} ${formatMs(dur)}` : ''}
                  </div>
                </Tooltip>
              );
            })}
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 10 }}>
        {types.map((t) => (
          <span key={t} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: '#5E6E88', fontSize: 11 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: TYPE_COLORS[t] || '#64748B', display: 'inline-block' }} />
            {TYPE_LABELS[t] || t}
          </span>
        ))}
      </div>

      {selected && (
        <div style={{ marginTop: 12, background: '#0D1525', border: '1px solid #1A2748', borderRadius: 8, padding: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ color: '#E4EBF5', fontWeight: 500, fontSize: 13 }}>{selected.operation_name}</span>
            <span style={{ color: '#5E6E88', fontFamily: 'monospace', fontSize: 12 }}>{formatMs(durationOf(selected))}</span>
          </div>
          <div style={{ color: '#5E6E88', fontSize: 12, marginBottom: 4 }}>
            {TYPE_LABELS[selected.span_type] || selected.span_type} · 层级 {depths.get(selected.span_id) ?? 0} · 自身耗时{' '}
            {formatMs(selfTimes.get(selected.span_id) ?? 0)}
          </div>
          {selected.error_message && <div style={{ color: '#EF4444', fontSize: 12 }}>错误：{selected.error_message}</div>}
          {selected.inputs && Object.keys(selected.inputs).length > 0 && (
            <div style={{ color: '#94A3B8', fontSize: 12, marginTop: 4 }}>
              <span style={{ color: '#5E6E88' }}>输入：</span>
              {Object.entries(selected.inputs)
                .map(([k, v]) => `${k}=${typeof v === 'string' ? v.substring(0, 100) : JSON.stringify(v)}`)
                .join(', ')}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default FlameGraph;
