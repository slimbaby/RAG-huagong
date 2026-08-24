"""
TableProcessor 单元测试
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tools.table_processor import TableProcessor


class TestTableProcessor:
    """语义行分组测试"""

    def test_small_table_single_chunk(self):
        """小表（<=20行）应返回单个 chunk"""
        processor = TableProcessor()
        headers = ["姓名", "年龄", "城市"]
        rows = [
            ["张三", "28", "杭州"],
            ["李四", "30", "上海"],
            ["王五", "25", "北京"],
        ]
        chunks = processor.process_table(headers, rows, "test.xlsx", "xlsx")
        assert len(chunks) == 1
        assert "张三" in chunks[0].content
        assert chunks[0].metadata.content_type == "table"

    def test_semantic_grouping_by_category(self):
        """按类别列语义分组"""
        processor = TableProcessor(enable_semantic_grouping=True, max_rows_per_subgroup=3)
        headers = ["名称", "类别", "价格"]
        rows = [
            ["产品A", "电子", "100"],
            ["产品B", "电子", "200"],
            ["产品C", "食品", "50"],
            ["产品D", "食品", "60"],
            ["产品E", "电子", "150"],
            ["产品F", "食品", "30"],
        ]
        chunks = processor.process_table(headers, rows, "test.csv", "csv")
        # 应该按"类别"分为2组
        assert len(chunks) == 2
        assert "电子" in chunks[0].metadata.section_title
        assert "食品" in chunks[1].metadata.section_title

    def test_semantic_grouping_by_keyword_header(self):
        """表头包含关键词时自动识别分组键"""
        processor = TableProcessor(enable_semantic_grouping=True, max_rows_per_subgroup=3)
        headers = ["订单号", "客户名称", "金额"]
        rows = [
            ["001", "张三", "5000"],
            ["002", "李四", "3000"],
            ["003", "张三", "2000"],
            ["004", "李四", "4000"],
            ["005", "张三", "1000"],
            ["006", "李四", "6000"],
        ]
        chunks = processor.process_table(headers, rows, "orders.xlsx", "xlsx")
        # 应该按"客户名称"分为2组
        assert len(chunks) == 2
        assert "张三" in chunks[0].metadata.section_title
        assert "李四" in chunks[1].metadata.section_title

    def test_no_viable_group_key_fallback(self):
        """无合适分组键时退回固定行数切分"""
        processor = TableProcessor(
            enable_semantic_grouping=True,
            max_rows_per_subgroup=3,
            max_chunk_chars=100,
        )
        headers = ["ID", "数据"]
        rows = [[str(i), f"value_{i}"] for i in range(10)]
        chunks = processor.process_table(headers, rows, "test.csv", "csv")
        # ID 列基数=行数，无法分组，应按3行一组切分
        assert len(chunks) >= 3
        for chunk in chunks:
            assert chunk.metadata.content_type == "table"

    def test_semantic_grouping_disabled(self):
        """禁用语义分组时走固定行数切分"""
        processor = TableProcessor(
            enable_semantic_grouping=False,
            max_rows_per_subgroup=3,
            max_chunk_chars=100,
        )
        headers = ["类别", "值"]
        rows = [["A", str(i)] for i in range(10)]
        chunks = processor.process_table(headers, rows, "test.csv", "csv")
        # 禁用分组，按3行切
        assert len(chunks) >= 3

    def test_group_exceeding_max_chars_subsplit(self):
        """超大组应被子切分"""
        processor = TableProcessor(
            max_chunk_chars=200,
            max_rows_per_subgroup=5,
            enable_semantic_grouping=True,
        )
        headers = ["类别", "描述"]
        rows = [["A", f"描述内容_{i}"] for i in range(20)]
        rows += [["B", f"描述内容_{i}"] for i in range(20)]
        chunks = processor.process_table(headers, rows, "test.csv", "csv")
        # A 组 20 行，超过 5 行限制，应被子切分
        assert len(chunks) >= 2

    def test_table_to_markdown(self):
        """Markdown 表格生成"""
        headers = ["姓名", "年龄"]
        rows = [["张三", "28"], ["李四", "30"]]
        md = TableProcessor.table_to_markdown(headers, rows)
        assert "| 姓名 | 年龄 |" in md
        assert "| --- | --- |" in md
        assert "| 张三 | 28 |" in md
        assert "| 李四 | 30 |" in md

    def test_table_to_markdown_ragged_rows(self):
        """列数不一致的行应正确处理"""
        headers = ["A", "B", "C"]
        rows = [["1", "2", "3"], ["4", "5"]]
        md = TableProcessor.table_to_markdown(headers, rows)
        assert "| 4 | 5 |  |" in md

    def test_table_to_natural_language_with_group(self):
        """带分组标签的自然语言描述"""
        headers = ["姓名", "年龄"]
        rows = [["张三", "28"], ["李四", "30"]]
        nl = TableProcessor.table_to_natural_language(headers, rows, group_label="部门: 技术部")
        assert "部门: 技术部" in nl
        assert "2条记录" in nl

    def test_table_to_natural_language_without_group(self):
        """不带分组标签的自然语言描述"""
        headers = ["姓名", "年龄"]
        rows = [["张三", "28"]]
        nl = TableProcessor.table_to_natural_language(headers, rows)
        assert "张三" in nl
        assert "28" in nl

    def test_empty_headers(self):
        """空表头应返回空列表"""
        processor = TableProcessor()
        chunks = processor.process_table([], [["data"]], "test.csv", "csv")
        assert chunks == []

    def test_empty_rows(self):
        """空行应返回空列表"""
        processor = TableProcessor()
        chunks = processor.process_table(["col1", "col2"], [], "test.csv", "csv")
        assert chunks == []

    def test_single_column_table(self):
        """单列表不应尝试分组，走小表路径"""
        processor = TableProcessor(enable_semantic_grouping=True, max_rows_per_subgroup=5)
        headers = ["数据"]
        rows = [[str(i)] for i in range(4)]
        chunks = processor.process_table(headers, rows, "test.csv", "csv")
        # 单列无法有效分组，4行 <= max_rows_per_subgroup=5，走小表路径
        assert len(chunks) == 1

    def test_section_title_preserved(self):
        """section_title 应被保留"""
        processor = TableProcessor(max_rows_per_subgroup=5)
        headers = ["A", "B"]
        rows = [[str(i), f"v{i}"] for i in range(3)]
        chunks = processor.process_table(
            headers, rows, "test.xlsx", "xlsx",
            section_title="工作表: Sheet1",
        )
        assert "工作表: Sheet1" in chunks[0].metadata.section_title

    def test_group_label_in_section_title(self):
        """语义分组的 group_label 应出现在 section_title 中"""
        processor = TableProcessor(enable_semantic_grouping=True, max_rows_per_subgroup=3)
        headers = ["产品", "类别"]
        rows = [["A", "电子"], ["B", "电子"], ["C", "食品"], ["D", "食品"],
                ["E", "电子"], ["F", "食品"]]
        chunks = processor.process_table(
            headers, rows, "test.xlsx", "xlsx",
            section_title="工作表: Sheet1",
        )
        assert "类别: 电子" in chunks[0].metadata.section_title
        assert "类别: 食品" in chunks[1].metadata.section_title


class TestDetectGroupColumn:
    """分组键检测测试"""

    def test_keyword_match(self):
        """表头包含关键词时应返回该列索引"""
        processor = TableProcessor()
        headers = ["编号", "类别", "价格"]
        rows = [["1", "A", "100"]] * 10 + [["2", "B", "200"]] * 10
        col = processor._detect_group_column(headers, rows)
        # "编号"和"类别"都匹配关键词，"编号"基数=2，"类别"基数=2，先匹配到"编号"(col 0)
        assert col == 0

    def test_keyword_match_prefers_lowest_cardinality(self):
        """多个关键词匹配时选基数最低的"""
        processor = TableProcessor()
        headers = ["编号", "类别", "价格"]
        # "编号"基数=10，"类别"基数=2
        rows = [[str(i), "A" if i < 5 else "B", str(i * 100)] for i in range(10)]
        col = processor._detect_group_column(headers, rows)
        assert col == 1  # "类别"列基数更低

    def test_lowest_cardinality_fallback(self):
        """无关键词匹配时选最低基数列"""
        processor = TableProcessor()
        headers = ["X", "Y", "Z"]
        # X: {a,b}=2, Y: {1}=1, Z: {x,y}=2
        rows = [["a", "1", "x"]] * 5 + [["b", "1", "y"]] * 5
        col = processor._detect_group_column(headers, rows)
        # Y 基数=1 不满足>=2，X 基数=2 先被选中
        assert col == 0

    def test_too_few_rows(self):
        """行数<4时返回 None"""
        processor = TableProcessor()
        headers = ["类别", "值"]
        rows = [["A", "1"], ["B", "2"]]
        col = processor._detect_group_column(headers, rows)
        assert col is None

    def test_all_unique_values(self):
        """所有列基数=行数时返回 None"""
        processor = TableProcessor()
        headers = ["ID", "名称"]
        rows = [[str(i), f"name_{i}"] for i in range(20)]
        col = processor._detect_group_column(headers, rows)
        assert col is None


class TestGroupRowsByColumn:
    """行分组测试"""

    def test_basic_grouping(self):
        """基本分组功能"""
        headers = ["类别", "值"]
        rows = [["A", "1"], ["B", "2"], ["A", "3"]]
        groups = TableProcessor._group_rows_by_column(headers, rows, 0)
        assert len(groups) == 2
        assert groups[0][0] == "A"
        assert len(groups[0][1]) == 2
        assert groups[1][0] == "B"
        assert len(groups[1][1]) == 1

    def test_empty_values_grouped(self):
        """空值应被归为一组"""
        headers = ["类别", "值"]
        rows = [["", "1"], ["A", "2"], ["", "3"]]
        groups = TableProcessor._group_rows_by_column(headers, rows, 0)
        assert len(groups) == 2
        assert groups[0][0] == ""
        assert len(groups[0][1]) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
