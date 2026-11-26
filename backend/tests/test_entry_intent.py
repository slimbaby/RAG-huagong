"""入园意图规则：共享用例表双端一致性 + 单一判定入口回归。

用例表位于 shared/entry_intent_cases.json，前端 vitest 读取同一份文件，
任一侧改规则都必须同步另一侧并更新用例表。
"""
import json
import os

from app.services.query_rewriter import QueryRewriter
from app.api.chat import _derive_entry_action

CASES_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "shared", "entry_intent_cases.json")
)


def _cases():
    with open(CASES_PATH, encoding="utf-8") as f:
        return json.load(f)["cases"]


def test_shared_cases_exist():
    assert len(_cases()) >= 40


def test_backend_decision_matches_shared_cases():
    for c in _cases():
        assert QueryRewriter.classify_entry_action(c["q"]) is c["action"], c["q"]


def test_backend_ambiguous_matches_shared_cases():
    for c in _cases():
        assert QueryRewriter.is_entry_ambiguous(c["q"]) is c["ambiguous"], c["q"]


def test_rule_ids_are_named_and_priority_declared():
    declared = {name for name, _ in QueryRewriter.ENTRY_INTENT_RULES}
    assert {"P0_strong_head", "P1_readonly", "P2_action"} <= declared
    for c in _cases():
        _, rid = QueryRewriter.classify_entry_detail(c["q"])
        assert rid == "none" or rid.startswith(("P0_", "P1_", "P2_")), (c["q"], rid)


def test_api_layer_is_single_decision_entry():
    """API 层无会话上下文时，结果必须等于规则流水线，不再有外层只读短路。"""
    for c in _cases():
        assert _derive_entry_action(c["q"], "session_1", None, force=False) is c["action"], c["q"]


def test_force_overrides_readonly():
    """选择卡里本人点了「办理」：即使是只读/两可句也强制进入园 Agent。"""
    assert _derive_entry_action("能帮我申请入园吗？", "session_1", None, force=True) is True
    assert _derive_entry_action("入园要带什么材料", "session_1", None, force=True) is True


def test_session_fallback_is_tightened():
    """入园会话里的补信息轮续留 Agent；闲聊/只读退回 RAG。"""
    assert _derive_entry_action("车牌鲁A12345", "entry-1", {"driver_name": "张三"}) is True
    assert _derive_entry_action("今天天气怎么样", "entry-1", {"driver_name": "张三"}) is False
    assert _derive_entry_action("入园有什么规定", "entry-1", {"driver_name": "张三"}) is False
    assert _derive_entry_action("入园要带什么材料", "session_1", None) is False


def test_normalization_handles_full_width_chars():
    assert QueryRewriter.classify_entry_action("　帮我申请入园　") is True
    assert QueryRewriter.is_entry_ambiguous("　能帮我申请入园吗？ ") is True
