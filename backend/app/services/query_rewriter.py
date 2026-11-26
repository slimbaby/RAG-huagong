"""
查询优化器 - 处理用户随意输入
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import asyncio
import json
import re
import logging

from ..prompts.query import (
    build_expand_prompt,
    build_rewrite_and_route_prompt,
    build_rewrite_prompt,
)

logger = logging.getLogger(__name__)


class QueryType(Enum):
    FACTUAL = "factual"
    PROCEDURAL = "procedural"
    COMPARISON = "comparison"
    TROUBLESHOOTING = "troubleshooting"
    REGULATORY = "regulatory"
    DATA_LOOKUP = "data_lookup"


@dataclass
class OptimizedQuery:
    original: str
    rewritten: str
    expanded_queries: List[str]
    query_type: QueryType
    keywords: List[str]
    intent_confidence: float
    optimization_notes: List[str]
    intent: str = "kb_query"  # "kb_query" | "general"
    usage: Dict = None  # {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    sub_queries: List[str] = None  # 比较类查询分解出的子查询列表


class QueryRewriter:
    """查询优化器 - 处理用户随意输入"""

    def __init__(self, llm_client=None):
        self.llm = llm_client
        self.colloquial_patterns = [
            (r'咋|咋整|咋办|怎么搞|肿么办', '怎么办'),
            (r'啥|啥子|什么东东', '什么'),
            (r'哪嘎|哪块|哪边|哪儿', '哪里'),
            (r'多少|多些|几多', '多少'),
            (r'行不|可以不|能不|ok不', '是否可以'),
            (r'好不好|中不中|成不成', '是否可行'),
            (r'pls|plz', '请'),
            (r'thx|thks|3q', '谢谢'),
            (r'反应釜|釜|反应锅', '反应釜'),
            (r'精馏|蒸馏', '精馏'),
            (r'安阀|安全阀门', '安全阀'),
            (r'联锁|连锁', '联锁'),
            (r'HAZOP|哈兹普|危害分析', 'HAZOP分析'),
        ]
        self.query_stopwords = {
            '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很',
            '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这', '他', '吗', '啊', '呢',
            '吧', '嗯', '哦', '呃', '额', '然后', '就是', '那个', '这个', '我想问', '请问', '麻烦问',
        }

    async def optimize(self, raw_query: str) -> OptimizedQuery:
        optimization_notes = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        logger.info(f"[optimize] raw_query='{raw_query}', llm={'有' if self.llm else '无'}")
        # 用原始查询做意图分类（清洗可能丢失中文字符）
        intent = self._classify_query_intent(raw_query)
        optimization_notes.append(f"意图分类：{intent}")
        cleaned = self._basic_clean(raw_query)
        if cleaned != raw_query:
            optimization_notes.append("基础清洗：去除多余空白和特殊字符")
        normalized = self._normalize_colloquial(cleaned)
        if normalized != cleaned:
            optimization_notes.append("口语转换：将口语表达转为标准表达")
        logger.info(f"[optimize] cleaned='{cleaned}', normalized='{normalized}', raw='{raw_query}'")
        query_type, confidence = await self._classify_intent(normalized)
        optimization_notes.append(f"意图识别：{query_type.value}，置信度{confidence:.2f}")
        keywords = self._extract_keywords(normalized)
        optimization_notes.append(f"关键词提取：{', '.join(keywords)}")
        # 通用闲聊/问候/业务查询：跳过 LLM 改写和扩展（节省 ~26s）
        if intent in ("general", "business_query"):
            optimization_notes.append(f"{intent}，跳过LLM改写")
            return OptimizedQuery(
                original=raw_query, rewritten=normalized, expanded_queries=[],
                query_type=query_type, keywords=keywords, intent_confidence=confidence,
                optimization_notes=optimization_notes, intent=intent, usage=total_usage,
                sub_queries=[],
            )
        sub_queries = []
        if self.llm:
            if query_type == QueryType.COMPARISON:
                # 比较类：单次 LLM 调用（改写+路由+分解），跳过 _expand_queries 节省一次调用
                (rewritten, route_from_llm, sub_queries, rewrite_usage) = \
                    await self._llm_rewrite_and_route(normalized, query_type, keywords)
                expanded = []
                expand_usage = {}
                logger.info(f"[optimize] COMPARISON sub_queries={sub_queries}")
            else:
                # 非比较类：简单查询跳过扩展（节省 ~15s LLM 调用）
                if query_type in (QueryType.FACTUAL, QueryType.DATA_LOOKUP) and len(normalized) < 50:
                    (rewritten, route_from_llm, _, rewrite_usage) = await self._llm_rewrite_and_route(normalized, query_type, keywords)
                    expanded = []
                    expand_usage = {}
                    optimization_notes.append("简单查询，跳过扩展")
                else:
                    (rewritten, route_from_llm, _, rewrite_usage), (expanded, expand_usage) = \
                        await asyncio.gather(
                            self._llm_rewrite_and_route(normalized, query_type, keywords),
                            self._expand_queries(normalized, query_type),
                        )
            logger.info(f"[optimize] llm_rewritten='{rewritten}', route_from_llm='{route_from_llm}'")
            if rewritten:
                if rewritten != normalized:
                    optimization_notes.append("LLM改写：修正语法、补充完整")
                if route_from_llm:
                    # 用户明确要求查知识库时，不听LLM路由，强制走kb_query
                    kb_explicit = any(kw in raw_query for kw in ['根据知识库', '知识库里', '知识库中', '知识库文档', '文档里', '文档中'])
                    if kb_explicit and route_from_llm == "general" and intent == "kb_query":
                        optimization_notes.append(f"用户明确要求查知识库，忽略LLM路由general，保持kb_query")
                    elif route_from_llm == "kb_query" and intent == "general":
                        intent = route_from_llm
                        optimization_notes.append(f"LLM路由：{intent}（覆盖regex general）")
                    elif route_from_llm != intent:
                        intent = route_from_llm
                        optimization_notes.append(f"LLM路由：{intent}")
                    else:
                        optimization_notes.append(f"LLM路由={route_from_llm}，与regex一致")
            else:
                rewritten = normalized
                expand_usage = rewrite_usage
                sub_queries = []
                optimization_notes.append("LLM改写失败，使用原始查询")
            for k in total_usage:
                total_usage[k] += rewrite_usage.get(k, 0)
        else:
            rewritten = normalized
            expanded = []
            expand_usage = {}
        for k in total_usage:
            total_usage[k] += expand_usage.get(k, 0)
        if expanded:
            optimization_notes.append(f"查询扩展：生成{len(expanded)}个变体")
        if sub_queries:
            optimization_notes.append(f"子查询分解：{len(sub_queries)}个子查询 {sub_queries}")
        logger.info(f"[optimize] final rewritten='{rewritten}', intent={intent}")
        return OptimizedQuery(original=raw_query, rewritten=rewritten, expanded_queries=expanded, query_type=query_type, keywords=keywords, intent_confidence=confidence, optimization_notes=optimization_notes, intent=intent, usage=total_usage, sub_queries=sub_queries)

    def _basic_clean(self, text: str) -> str:
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        text = text.replace('？', '?').replace('，', ',')
        text = re.sub(r'[?!？！]{2,}', '?', text)
        if text and text[-1] not in '.。?？!！':
            text += '?'
        return text

    def _normalize_colloquial(self, text: str) -> str:
        for pattern, replacement in self.colloquial_patterns:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    async def _classify_intent(self, query: str) -> Tuple[QueryType, float]:
        query_lower = query.lower()
        if any(kw in query_lower for kw in ['怎么办', '为什么', '原因', '解决', '故障', '报警', '异常']):
            return QueryType.TROUBLESHOOTING, 0.8
        if any(kw in query_lower for kw in ['怎么做', '步骤', '方法', '流程', '操作']):
            return QueryType.PROCEDURAL, 0.8
        if any(kw in query_lower for kw in ['区别', '对比', '比较', '哪个', '谁大', '谁小', '谁高', '谁低', '谁多', '谁少', '谁更', '谁最', '更大', '更小', '更高', '更低', '最多', '最少']):
            return QueryType.COMPARISON, 0.8
        if any(kw in query_lower for kw in ['规定', '标准', '要求', '法规', '规范']):
            return QueryType.REGULATORY, 0.8
        if any(kw in query_lower for kw in ['多少', '参数', '数值', '温度', '压力', '流量']):
            return QueryType.DATA_LOOKUP, 0.7
        return QueryType.FACTUAL, 0.6

    # ===== 入园办理意图：命名规则表 + 优先级流水线 =====
    # 只用 Python re 与 JS RegExp 都支持的子集（字符类、^、.{0,N}、|、非捕获组），
    # 前后端行为由 shared/entry_intent_cases.json + pytest/vitest 一致性用例锁死。
    # 顺序：P0 句首办理意图 → P1 只读咨询 → P2 通用办理模式，命中即短路。
    ENTRY_INTENT_RULES = (
        ("P0_strong_head", "action"),
        ("P1_readonly", "readonly"),
        ("P2_action", "action"),
    )
    ENTRY_STRONG_HEAD_RE = re.compile(
        r'^\s*(?:师傅|你好|您好|嗨|嗯|那个)?[\s，,]*'
        r'(?:帮我|我要|我想|给我|麻烦|请帮我|能帮我|可以帮我|来)'
        r'.{0,8}'
        r'(?:申请|办理|帮我办|提交|预约|报备|登记|办一下|办|放行|弄|搞)'
    )
    ENTRY_DOMAIN_RE = re.compile(
        r'(?:入园|进园|入厂|进厂|园区|通行证|入园证|手续|证件|照片|材料)'
    )
    ENTRY_QUESTION_TAIL_RE = re.compile(r'(?:吗|么|呢)[？?]?\s*$|[？?]\s*$')
    ENTRY_ACTION_PATTERNS = (
        # 办理动词 + 领域对象
        r'(?:申请|办理|帮我办|提交|预约|报备|办一下|登记).{0,10}(?:入园|进园|入厂|进厂|园区|通行证|手续)',
        # 倒装：领域对象 + 动作（含报备/登记）
        r'(?:入园|进园|入厂|进厂).{0,10}(?:申请|预约|办理|通行证|手续|报备|登记)',
        # 我要/我想 + 入园/进厂
        r'^(?:帮我|我要|我想).{0,12}(?:入园|进园|入厂|进厂)',
        # 补材料轮（本次唯一纳入的场景词）：证件/照片已备 + 提交/帮我
        r'(?:证件|照片|材料).{0,12}(?:拍好|传好|都齐|都备齐|准备好了).{0,12}(?:提交|帮我|申请|办)',
    )
    ENTRY_READONLY_PATTERNS = (
        r'^(?:查|查询|查一下|查查|看看|问一下|请问|想问|咨询|了解一下)',
        r'(?:怎么|如何|怎样|需要什么|要带什么|带哪些|哪些证件|哪些材料|什么证件|什么材料|需要哪些|流程是什么|什么流程)',
        r'(?:规定|制度|要求|标准|政策|流程说明|入园须知)',
        r'(?:进度|状态|结果|通过了吗|批了吗|审核到哪)',
        r'(?:吗|么|呢)[？?]?\s*$',
    )

    @staticmethod
    def normalize_intent_text(query: str) -> str:
        """归一化：全角空格 / 中英标点 / 连续空白 / 小写，保证 ^ 锚与窗口稳定。"""
        q = (query or "").replace("\u3000", " ").replace("？", "?").replace("，", ",")
        return re.sub(r"\s+", " ", q).strip().lower()

    @classmethod
    def is_entry_read_only(cls, query: str) -> bool:
        """只读咨询（规定/流程/进度/疑问句）→ 留在 RAG 知识库问答。"""
        q = cls.normalize_intent_text(query)
        return any(re.search(p, q) for p in cls.ENTRY_READONLY_PATTERNS)

    @classmethod
    def is_entry_ambiguous(cls, query: str) -> bool:
        """句首办理意图 + 疑问语气结尾：不擅判，交前端选择卡由司机本人决定。"""
        q = cls.normalize_intent_text(query)
        return bool(
            cls.ENTRY_STRONG_HEAD_RE.search(q)
            and cls.ENTRY_DOMAIN_RE.search(q)
            and cls.ENTRY_QUESTION_TAIL_RE.search(q)
        )

    @classmethod
    def classify_entry_detail(cls, query: str) -> Tuple[bool, str]:
        """优先级流水线，返回 (是否办理, 命中规则 id)，供单测与 trace 观测。"""
        q = cls.normalize_intent_text(query)
        # P0：句首第一人称办理请求优先于只读规则；疑问语气结尾留给前端选择卡。
        if (cls.ENTRY_STRONG_HEAD_RE.search(q)
                and cls.ENTRY_DOMAIN_RE.search(q)
                and not cls.ENTRY_QUESTION_TAIL_RE.search(q)):
            return True, "P0_strong_head"
        # P1：只读咨询
        if any(re.search(p, q) for p in cls.ENTRY_READONLY_PATTERNS):
            return False, "P1_readonly"
        # P2：通用办理模式（含倒装、进厂、补材料轮）
        for i, p in enumerate(cls.ENTRY_ACTION_PATTERNS):
            if re.search(p, q):
                return True, f"P2_action_{i}"
        return False, "none"

    @classmethod
    def classify_entry_action(cls, query: str) -> bool:
        """前置轻量意图闸：命中「办申请」才切到入园 Agent。

        纯规则、零额外模型调用、可离线运行；接口保持返回 bool，
        三态中的 ambiguous 由前端规则表 + 选择卡处理（方案 B）。
        """
        return cls.classify_entry_detail(query)[0]

    def _classify_query_intent(self, query: str) -> str:
        """判断用户问题是知识库查询、业务查询还是通用问题。返回 'kb_query'、'business_query' 或 'general'。"""
        query_lower = query.lower().strip()

        # 通用问候/闲聊
        if re.match(r'^(你好|hello|hi|嗨|hey|早上好|下午好|晚上好)', query_lower):
            return "general"

        # 入园预约是事务型请求：直接走 generate + function calling。
        # 否则「园区 / 审批 / 许可证」会命中下方 kb_keywords，白跑一轮向量检索，
        # 还会把检索到的制度条文塞进 prompt 干扰工具调用。
        if self.classify_entry_action(query_lower):
            return "business_query"

        # 业务查询关键词（订单/库存/质检）
        business_keywords = [
            '订单', '下单', '交期', '发货', '客户订单', '订单状态', '订单号',
            '库存', '物料', '仓库', '入库', '出库', '安全库存', '库存量',
            '质检', '检验', '合格', '不合格', '批次', '批次号', '质检结果',
            '采购', '供应商', '采购单',
        ]
        has_business = any(kw in query_lower for kw in business_keywords)
        if has_business:
            return "business_query"

        # 通用知识关键词（无领域指向）
        general_patterns = [
            r'(谁是|什么是|在哪|什么时候|多少年|第几届)',
            r'(天气|时间|日期|新闻|股票)',
            r'(谢谢|感谢|再见|拜拜)',
            r'(奥运会|世界杯|世博会|亚运会)',
            r'(北京|上海|广州|深圳|中国|美国|日本|欧洲)',
        ]
        has_general = any(re.search(p, query_lower) for p in general_patterns)

        # 知识库领域关键词
        kb_keywords = [
            '知识库', '文档', '规定', '标准', '制度', '规范', '法规', '文件',
            '园区', '化工', '安全', '环保', '排放', '消防', '应急', '预案',
            '设备', '管道', '阀门', '反应釜', '储罐', '装置', '工艺',
            '许可证', '审批', '备案', '检查', '隐患', '事故', '风险',
        ]
        has_kb = any(kw in query_lower for kw in kb_keywords)

        if has_general and not has_kb:
            return "general"

        return "kb_query"

    def _extract_keywords(self, text: str) -> List[str]:
        try:
            import jieba.analyse
            keywords = jieba.analyse.extract_tags(text, topK=5, withWeight=False)
            keywords = [kw for kw in keywords if kw not in self.query_stopwords]
            return keywords
        except ImportError:
            words = re.findall(r'[一-龥]+', text)
            return [w for w in words if len(w) >= 2 and w not in self.query_stopwords][:5]

    async def _llm_rewrite(self, query: str, query_type: QueryType, keywords: List[str]) -> str:
        prompt = build_rewrite_prompt(query, query_type.value, ', '.join(keywords))
        try:
            result = await self.llm.generate(prompt)
            if isinstance(result, dict):
                text = result.get("text", "").strip()
                usage = result.get("usage", {})
            else:
                text = str(result).strip()
                usage = {}
            return text, usage
        except Exception as e:
            logger.warning(f"LLM rewrite failed: {e}")
            return query, {}

    async def _llm_rewrite_and_route(self, query: str, query_type: QueryType, keywords: List[str]) -> Tuple[str, str, List[str], dict]:
        """LLM改写 + 路由分类 + 子查询分解（比较类）。返回 (rewritten, route, sub_queries, usage)；失败时 route=''。"""
        prompt = build_rewrite_and_route_prompt(
            query, query_type.value, ', '.join(keywords),
            is_comparison=(query_type == QueryType.COMPARISON),
        )
        try:
            result = await self.llm.generate(prompt)
            text = result.get("text", "") if isinstance(result, dict) else str(result)
            usage = result.get("usage", {}) if isinstance(result, dict) else {}
            m = re.search(r'\{[\s\S]*\}', text)
            if m:
                data = json.loads(m.group())
                rewritten = (data.get("rewritten") or "").strip()
                route = (data.get("route") or "").strip()
                # 提取子查询
                raw_subs = data.get("sub_queries") or []
                seen = set()
                sub_queries = []
                for sq in raw_subs:
                    sq = (sq or "").strip()
                    if sq and sq not in seen:
                        seen.add(sq)
                        sub_queries.append(sq)
                if rewritten:
                    return rewritten, route if route in ("kb_query", "general", "business_query") else "", sub_queries[:5], usage
            return "", "", [], usage
        except Exception as e:
            logger.warning(f"LLM rewrite+route failed: {e}")
            return "", "", [], {}

    async def _expand_queries(self, query: str, query_type: QueryType) -> List[str]:
        prompt = build_expand_prompt(query, query_type.value)
        try:
            result = await self.llm.generate(prompt)
            if isinstance(result, dict):
                text = result.get("text", "").strip()
                usage = result.get("usage", {})
            else:
                text = str(result).strip()
                usage = {}
            expanded = [q.strip() for q in text.split('\n') if q.strip()]
            seen = {query}
            unique_expanded = []
            for q in expanded:
                if q not in seen:
                    seen.add(q)
                    unique_expanded.append(q)
            return unique_expanded[:3], usage
        except Exception as e:
            logger.warning(f"Query expansion failed: {e}")
            return [], {}
