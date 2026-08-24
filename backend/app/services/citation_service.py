"""
引用服务 - 处理文档引用和来源追溯
"""

import re
from typing import List, Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class CitationService:
    """引用服务 - 支持引用解析、验证和时间标注"""

    CITATION_PATTERN = r'\[来源(\d+)(?::\s*(.*?))?\]'

    def format_context_with_citation(self, chunks: List[Dict]) -> str:
        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            meta = chunk.get("metadata", {})
            citation = f"[来源{i}: {meta.get('source_file', '未知')}"
            page = meta.get('page_number')
            if page and int(page) > 0:
                citation += f", 第{page}页"
            citation += "]"
            context_parts.append(f"--- {citation} ---\n{chunk.get('content', '')}\n")
        return "\n".join(context_parts)

    def format_context_with_citation_and_time(self, chunks: List[Dict]) -> str:
        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            meta = chunk.get("metadata", {})
            citation = f"[来源{i}: {meta.get('source_file', '未知')}"
            page = meta.get('page_number')
            if page and int(page) > 0:
                citation += f", 第{page}页"
            citation += "]"
            time_info = ""
            if meta.get("source_type") == "law_regulation":
                if meta.get("publish_date"):
                    time_info += f"，发布日期: {meta['publish_date']}"
                if meta.get("effective_date"):
                    time_info += f"，生效日期: {meta['effective_date']}"
                if meta.get("last_updated_at"):
                    try:
                        update_date = datetime.fromisoformat(meta["last_updated_at"])
                        time_info += f"，知识库更新: {update_date.strftime('%Y-%m-%d')}"
                    except:
                        time_info += f"，知识库更新: {meta['last_updated_at']}"
                if meta.get("version"):
                    time_info += f"，版本: v{meta['version']}"
            context_parts.append(f"--- {citation}{time_info} ---\n{chunk.get('content', '')}\n")
        return "\n".join(context_parts)

    def parse_citations(self, answer: str, source_chunks: List[Dict]) -> Dict:
        citations = []
        matches = re.finditer(self.CITATION_PATTERN, answer)
        for match in matches:
            source_num = int(match.group(1))
            detail = match.group(2) if match.group(2) else ""
            if 1 <= source_num <= len(source_chunks):
                chunk = source_chunks[source_num - 1]
                meta = chunk.get("metadata", {})
                citations.append({
                    "citation_id": source_num, "text": match.group(0), "detail": detail,
                    "chunk_id": chunk.get("chunk_id"), "source_file": meta.get("source_file"),
                    "page_number": meta.get("page_number"), "line_start": meta.get("line_start"),
                    "line_end": meta.get("line_end"), "content_preview": chunk.get("content", "")[:500],
                    "content": chunk.get("content", ""),
                })
        return {"answer": answer, "citations": citations, "citation_count": len(citations), "has_citations": len(citations) > 0}

    def validate_citations(self, answer: str, chunks: List[Dict]) -> Dict:
        parsed = self.parse_citations(answer, chunks)
        issues = []
        for citation in parsed["citations"]:
            if not citation["chunk_id"]:
                issues.append({"type": "missing_source", "citation": citation["text"], "message": "引用的来源不存在"})
        return {"is_valid": len(issues) == 0, "issues": issues, "citation_accuracy": 1 - (len(issues) / max(len(parsed["citations"]), 1))}
