"""
表格处理器 - 语义行分组，保持表格完整性
"""

from typing import List, Dict, Optional, Tuple
from collections import OrderedDict
import logging

from .base_tool import DocumentChunk

logger = logging.getLogger(__name__)

# 用于分组键检测的关键词
KEY_WORDS = {
    "id", "name", "order", "customer", "category", "type", "code",
    "product", "department", "group", "class", "status", "region",
    "编号", "名称", "类别", "类型", "客户", "订单", "分类",
    "部门", "产品", "状态", "设备", "物料", "区域", "城市",
    "姓名", "工号", "岗位", "职级",
}


class TableProcessor:
    """表格处理器 - 支持语义行分组"""

    def __init__(
        self,
        max_chunk_chars: int = 2048,
        max_rows_per_subgroup: int = 20,
        enable_semantic_grouping: bool = True,
    ):
        self.max_chunk_chars = max_chunk_chars
        self.max_rows_per_subgroup = max_rows_per_subgroup
        self.enable_semantic_grouping = enable_semantic_grouping

    def process_table(
        self,
        headers: List[str],
        rows: List[List[str]],
        source_file: str,
        file_type: str,
        chunk_index: int = 0,
        page_number: Optional[int] = None,
        section_title: Optional[str] = None,
    ) -> List[DocumentChunk]:
        """处理表格，返回 chunk 列表。优先语义分组，退回固定行数切分。"""
        if not headers or not rows:
            return []

        # 小表直接返回单 chunk
        if len(rows) <= self.max_rows_per_subgroup:
            return self._create_table_chunks(
                headers, rows, source_file, file_type,
                chunk_index, page_number, section_title,
            )

        # 尝试语义分组
        if self.enable_semantic_grouping:
            group_col = self._detect_group_column(headers, rows)
            if group_col is not None:
                groups = self._group_rows_by_column(headers, rows, group_col)
                if len(groups) > 1:
                    return self._create_grouped_chunks(
                        headers, groups, source_file, file_type,
                        chunk_index, page_number, section_title,
                        group_col,
                    )

        # 退回固定行数切分
        return self._create_table_chunks(
            headers, rows, source_file, file_type,
            chunk_index, page_number, section_title,
        )

    def _detect_group_column(
        self, headers: List[str], rows: List[List[str]]
    ) -> Optional[int]:
        """检测分组键列索引，返回 None 表示无合适分组键。"""
        num_rows = len(rows)
        if num_rows < 4:
            return None

        # Pass 1: 表头关键词匹配，选基数最低的
        best_col = None
        best_cardinality = float("inf")
        for col_idx, header in enumerate(headers):
            header_lower = header.lower().strip()
            matched = False
            for kw in KEY_WORDS:
                if kw in header_lower:
                    matched = True
                    break
            if matched:
                cardinality = self._column_cardinality(rows, col_idx)
                if 2 <= cardinality <= num_rows // 3 and cardinality < best_cardinality:
                    best_col = col_idx
                    best_cardinality = cardinality

        if best_col is not None:
            return best_col

        # Pass 2: 最低基数启发式
        for col_idx in range(len(headers)):
            cardinality = self._column_cardinality(rows, col_idx)
            if 2 <= cardinality <= num_rows // 3:
                return col_idx

        return None

    @staticmethod
    def _column_cardinality(rows: List[List[str]], col_idx: int) -> int:
        """计算指定列的 distinct value 数量。"""
        values = set()
        for row in rows:
            if col_idx < len(row):
                values.add(row[col_idx])
        return len(values)

    @staticmethod
    def _group_rows_by_column(
        headers: List[str], rows: List[List[str]], group_col: int
    ) -> List[Tuple[str, List[List[str]]]]:
        """按指定列分组，保持插入顺序。"""
        groups: OrderedDict[str, List[List[str]]] = OrderedDict()
        for row in rows:
            key = row[group_col] if group_col < len(row) else ""
            if key not in groups:
                groups[key] = []
            groups[key].append(row)
        return list(groups.items())

    def _create_grouped_chunks(
        self,
        headers: List[str],
        groups: List[Tuple[str, List[List[str]]]],
        source_file: str,
        file_type: str,
        chunk_index: int,
        page_number: Optional[int],
        section_title: Optional[str],
        group_col: int,
    ) -> List[DocumentChunk]:
        """为每个语义分组创建 chunk。"""
        chunks = []
        col_name = headers[group_col]
        for group_value, group_rows in groups:
            group_label = f"{col_name}: {group_value}"
            group_chunks = self._create_table_chunks(
                headers, group_rows, source_file, file_type,
                chunk_index, page_number, section_title,
                group_label=group_label,
            )
            chunks.extend(group_chunks)
            chunk_index += len(group_chunks)
        return chunks

    def _create_table_chunks(
        self,
        headers: List[str],
        rows: List[List[str]],
        source_file: str,
        file_type: str,
        chunk_index: int,
        page_number: Optional[int],
        section_title: Optional[str],
        group_label: Optional[str] = None,
    ) -> List[DocumentChunk]:
        """创建表格 chunk，超出预算时子切分。"""
        chars = self._estimate_chunk_chars(headers, rows)
        if chars <= self.max_chunk_chars or len(rows) <= self.max_rows_per_subgroup:
            return [self._make_chunk(
                headers, rows, source_file, file_type,
                chunk_index, page_number, section_title, group_label,
            )]

        # 子切分
        chunks = []
        for i in range(0, len(rows), self.max_rows_per_subgroup):
            sub_rows = rows[i : i + self.max_rows_per_subgroup]
            sub_label = group_label
            if group_label:
                sub_label = f"{group_label} (第{i // self.max_rows_per_subgroup + 1}部分)"
            else:
                sub_label = f"行{i+1}-{min(i + self.max_rows_per_subgroup, len(rows))}"
            chunk = self._make_chunk(
                headers, sub_rows, source_file, file_type,
                chunk_index, page_number, section_title, sub_label,
            )
            chunks.append(chunk)
            chunk_index += 1
        return chunks

    def _make_chunk(
        self,
        headers: List[str],
        rows: List[List[str]],
        source_file: str,
        file_type: str,
        chunk_index: int,
        page_number: Optional[int],
        section_title: Optional[str],
        group_label: Optional[str],
    ) -> DocumentChunk:
        """构造单个 DocumentChunk。"""
        table_md = self.table_to_markdown(headers, rows)
        table_nl = self.table_to_natural_language(headers, rows, group_label)
        final_section = section_title
        if group_label:
            final_section = f"{section_title} - {group_label}" if section_title else group_label
        return DocumentChunk.create(
            content=table_md,
            source_file=source_file,
            file_type=file_type,
            chunk_index=chunk_index,
            page_number=page_number,
            content_type="table",
            table_nl_description=table_nl,
            section_title=final_section,
        )

    @staticmethod
    def _estimate_chunk_chars(headers: List[str], rows: List[List[str]]) -> int:
        """估算 markdown 表格字符数。"""
        header_len = len(" | ".join(headers)) + 4
        sep_len = len(" | ".join(["---"] * len(headers))) + 4
        data_len = sum(len(" | ".join(row[:len(headers)])) + 4 for row in rows)
        return header_len + sep_len + data_len + len(rows) * 2

    @staticmethod
    def table_to_markdown(headers: List[str], rows: List[List[str]]) -> str:
        """表头 + 行数据 → Markdown 表格。"""
        if not headers:
            return ""
        md = "| " + " | ".join(headers) + " |\n"
        md += "| " + " | ".join(["---"] * len(headers)) + " |\n"
        for row in rows:
            # 对齐列数，多的截断，少的补空
            cells = row[:len(headers)]
            cells += [""] * (len(headers) - len(cells))
            md += "| " + " | ".join(cells) + " |\n"
        return md

    @staticmethod
    def table_to_natural_language(
        headers: List[str],
        rows: List[List[str]],
        group_label: Optional[str] = None,
    ) -> str:
        """表格 → 自然语言描述，可选分组前缀。"""
        if not headers or not rows:
            return ""
        prefix = ""
        if group_label:
            prefix = f"以下数据为{group_label}的{len(rows)}条记录。"
        descriptions = []
        for row in rows[:10]:
            parts = []
            for h, v in zip(headers, row):
                if v:
                    parts.append(f"{h}为{v}")
            if parts:
                descriptions.append("，".join(parts))
        return prefix + "。".join(descriptions)

    def extract_structured_data(
        self, headers: List[str], rows: List[List[str]]
    ) -> List[Dict]:
        """表格 → 结构化字典列表。"""
        return [
            {h: v for h, v in zip(headers, row) if h}
            for row in rows
        ]
