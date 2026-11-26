/**
 * @deprecated 已由通用 FlowChoiceCard 取代。
 * 前端意图规则表（utils/entryIntent.ts）已退役：判定收归后端，
 * 候选由 /api/chat/query/stream 的 intent_choice 事件下发。
 */
export { default } from '../Flow/FlowChoiceCard';
