"""
Agent 工具集 - 供 LLM function calling 使用的只读工具

设计原则:
- 全部只读，无副作用
- 员工信息从 xlsx 构建本地 SQLite（文件变更自动重建），数值查询走精确匹配，
  根治 LLM 直接读表格时的行错位/幻觉问题
"""

import ast
import glob
import json
import logging
import os
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

_EMPLOYEE_DB_PATH = "./checkpoints/employee_info.db"
_employee_db_ready = False


def _find_employee_xlsx() -> str:
    for pattern in ("/app/uploads/*员工信息*.xlsx", "./uploads/*员工信息*.xlsx"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return ""


def _ensure_employee_db() -> bool:
    """首次调用时从 xlsx 构建 SQLite；xlsx 更新后自动重建。"""
    global _employee_db_ready
    if _employee_db_ready:
        return True
    xlsx = _find_employee_xlsx()
    if not xlsx:
        logger.warning("[agent_tools] 未找到员工信息.xlsx")
        return False
    try:
        from openpyxl import load_workbook

        wb = load_workbook(xlsx, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not rows:
            return False
        header = [str(h or "").strip() for h in rows[0]]

        def col(keyword: str) -> int:
            for i, h in enumerate(header):
                if keyword in h:
                    return i
            return -1

        c_name, c_city, c_age = col("姓名"), col("城市"), col("年龄")
        if c_name < 0:
            logger.warning(f"[agent_tools] 表头无法识别: {header}")
            return False
        if os.path.exists(_EMPLOYEE_DB_PATH):
            os.remove(_EMPLOYEE_DB_PATH)
        os.makedirs(os.path.dirname(_EMPLOYEE_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(_EMPLOYEE_DB_PATH)
        conn.execute("CREATE TABLE employees (name TEXT, city TEXT, age INTEGER)")
        count = 0
        for row in rows[1:]:
            name = row[c_name] if c_name < len(row) else None
            if not name:
                continue
            city = row[c_city] if 0 <= c_city < len(row) else ""
            age = row[c_age] if 0 <= c_age < len(row) else None
            conn.execute(
                "INSERT INTO employees VALUES (?, ?, ?)",
                (str(name).strip(), str(city or "").strip(), int(age) if age is not None else None),
            )
            count += 1
        conn.commit()
        conn.close()
        _employee_db_ready = True
        logger.info(f"[agent_tools] 员工数据库构建完成: {count} 条 ({xlsx})")
        return True
    except Exception as e:
        logger.error(f"[agent_tools] 员工数据库构建失败: {e}")
        return False


def tool_query_employee_info(name: str = "") -> str:
    """查询员工信息。name 为空返回全员列表；否则按姓名精确/模糊匹配。"""
    if not _ensure_employee_db():
        return json.dumps({"error": "员工数据库不可用"}, ensure_ascii=False)
    try:
        conn = sqlite3.connect(_EMPLOYEE_DB_PATH)
        if name.strip():
            rows = conn.execute(
                "SELECT name, city, age FROM employees WHERE name LIKE ?",
                (f"%{name.strip()}%",),
            ).fetchall()
        else:
            rows = conn.execute("SELECT name, city, age FROM employees").fetchall()
        conn.close()
        data = [{"姓名": r[0], "城市": r[1], "年龄": r[2]} for r in rows]
        return json.dumps({"count": len(data), "employees": data}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[agent_tools] 查询员工失败: {e}")
        return json.dumps({"error": f"查询失败: {e}"}, ensure_ascii=False)


_ALLOWED_AST_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
    ast.FloorDiv, ast.USub, ast.UAdd,
)


def tool_calculate(expression: str) -> str:
    """安全的四则运算计算器。expression 例如: '2026 - 1995' 或 '(18 + 31) / 2'"""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_AST_NODES):
                return json.dumps({"error": f"不支持的表达式元素: {type(node).__name__}"}, ensure_ascii=False)
        value = eval(compile(tree, "<calc>", "eval"))
        return json.dumps({"expression": expression, "result": value}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"计算失败: {e}"}, ensure_ascii=False)


def tool_get_current_date(_: str = "") -> str:
    """获取当前日期，用于年龄/年份推算。"""
    now = datetime.now()
    return json.dumps({
        "date": now.strftime("%Y-%m-%d"),
        "year": now.year,
        "month": now.month,
        "day": now.day,
    }, ensure_ascii=False)


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "query_employee_info",
            "description": "查询员工信息表（姓名、城市、年龄）。回答某人的年龄/城市/籍贯，或比较多人属性时必须优先使用此工具，不要凭表格记忆回答。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "员工姓名；留空返回全部员工"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "安全计算器，执行四则运算。需要根据出生年份推算年龄或比较数值时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "算术表达式，如 '2026 - 1995'"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_date",
            "description": "获取今天的日期和年份。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

TOOL_REGISTRY = {
    "query_employee_info": tool_query_employee_info,
    "calculate": tool_calculate,
    "get_current_date": tool_get_current_date,
}

TOOLS_SYSTEM_HINT = (
    "你可以调用以下工具辅助回答：query_employee_info(查员工表)、calculate(四则运算)、"
    "get_current_date(当前日期)。当问题涉及具体员工的年龄、城市等属性，或需要数值比较/年份推算时，"
    "必须先调用工具获取准确数据，再基于工具结果回答；禁止凭记忆猜测表格内容。"
)
