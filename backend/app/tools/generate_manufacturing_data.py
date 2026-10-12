"""生成制造业示例 Excel 数据文件。

运行方式: python -m app.tools.generate_manufacturing_data
生成位置: backend/app/data/订单数据.xlsx, 库存数据.xlsx, 质检数据.xlsx
"""

import os
import random
from datetime import datetime, timedelta
from pathlib import Path

try:
    from openpyxl import Workbook
except ImportError:
    raise ImportError("需要 openpyxl: pip install openpyxl")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── 化工行业真实数据配置 ──

CUSTOMERS = [
    ("浙江化工集团", "杭州市", "大型"),
    ("江苏新材料科技", "南京市", "中型"),
    ("上海精细化工", "上海市", "大型"),
    ("广东石化设备", "广州市", "中型"),
    ("山东恒通化工", "淄博市", "大型"),
    ("安徽皖维高新", "合肥市", "中型"),
    ("河南心连心化工", "新乡市", "大型"),
    ("湖北宜化集团", "宜昌市", "大型"),
    ("四川泸天化", "泸州市", "中型"),
    ("福建联合石化", "泉州市", "大型"),
]

PRODUCTS = {
    "聚乙烯颗粒": {"code": "PE-001", "spec": "HDPE 5000S", "unit": "吨", "price_range": (8500, 12000)},
    "聚丙烯颗粒": {"code": "PP-001", "spec": "T30S", "unit": "吨", "price_range": (7800, 11000)},
    "PVC树脂": {"code": "PVC-001", "spec": "SG-5", "unit": "吨", "price_range": (6500, 9000)},
    "环氧树脂": {"code": "EP-001", "spec": "E-51", "unit": "吨", "price_range": (18000, 28000)},
    "丙烯酸丁酯": {"code": "BA-001", "spec": "≥99.5%", "unit": "吨", "price_range": (9500, 14000)},
}

MATERIALS = [
    ("M001", "乙烯", "聚合级 ≥99.9%", "吨", 500, "原料仓A"),
    ("M002", "丙烯", "聚合级 ≥99.5%", "吨", 300, "原料仓A"),
    ("M003", "氯乙烯单体", "≥99.9%", "吨", 200, "原料仓A"),
    ("M004", "环氧氯丙烷", "≥99%", "桶(200kg)", 150, "原料仓A"),
    ("M005", "引发剂A", "过氧化二苯甲酰", "箱(20kg)", 80, "辅料仓C"),
    ("M006", "阻聚剂B", "对苯二酚", "箱(25kg)", 120, "辅料仓C"),
    ("M007", "稳定剂C", "硬脂酸钙", "袋(25kg)", 200, "辅料仓C"),
    ("M008", "包装袋(25kg)", "PE内膜+PP编织", "个", 5000, "成品仓B"),
    ("M009", "托盘", "1200×1000mm", "个", 300, "成品仓B"),
    ("M010", "标签纸", "不干胶 100×150mm", "卷(500张)", 1000, "成品仓B"),
]

INSPECTION_ITEMS = {
    "聚乙烯颗粒": [
        ("熔融指数", "g/10min", "2.0-3.0", 2.5),
        ("密度", "g/cm³", "0.945-0.955", 0.950),
        ("拉伸强度", "MPa", "≥25", 28.5),
        ("灰分", "%", "≤0.03", 0.015),
    ],
    "聚丙烯颗粒": [
        ("熔融指数", "g/10min", "2.5-4.0", 3.2),
        ("等规度", "%", "≥96", 97.5),
        ("拉伸强度", "MPa", "≥30", 33.0),
        ("灰分", "%", "≤0.02", 0.010),
    ],
    "PVC树脂": [
        ("聚合度", "-", "1000-1200", 1100),
        ("挥发分", "%", "≤0.3", 0.15),
        ("表观密度", "g/mL", "0.45-0.55", 0.50),
        ("筛余物(40目)", "%", "≤0.5", 0.2),
    ],
    "环氧树脂": [
        ("环氧值", "eq/100g", "0.48-0.54", 0.51),
        ("软化点", "℃", "12-20", 15),
        ("色泽", "号", "≤2", 1),
        ("挥发分", "%", "≤0.5", 0.2),
    ],
    "丙烯酸丁酯": [
        ("纯度", "%", "≥99.5", 99.7),
        ("酸值", "mgKOH/g", "≤0.10", 0.03),
        ("水分", "%", "≤0.10", 0.05),
        ("色泽(Pt-Co)", "号", "≤10", 5),
    ],
}

INSPECTORS = [
    ("张工", "质量部", "高级检验员"),
    ("李工", "质量部", "检验员"),
    ("王工", "质量部", "高级检验员"),
    ("赵工", "质量部", "检验员"),
    ("钱工", "技术部", "工程师"),
]

ORDER_STATUSES = ["待采购", "生产中", "已发货", "已完成", "已取消"]
RESPONSIBLE_PEOPLE = ["陈经理", "刘主管", "赵经理", "孙主管", "周经理", "吴总工"]


def generate_orders(wb: Workbook, seed: int = 42):
    """生成 50 条订单数据"""
    random.seed(seed)
    ws = wb.active
    ws.title = "订单数据"
    headers = ["订单号", "客户名称", "客户城市", "客户规模", "产品名称", "产品编码",
               "规格型号", "数量", "单位", "单价", "总金额", "状态",
               "下单日期", "交货日期", "负责人", "备注"]
    ws.append(headers)

    base_date = datetime(2025, 1, 1)
    remarks_pool = [
        "常规订单", "加急生产", "需质检报告", "客户指定批次",
        "含运费", "分批交货", "月度框架协议", "新客户首单",
        "", "", "", "",  # 空备注概率更高
    ]

    for i in range(1, 51):
        customer_name, city, scale = random.choice(CUSTOMERS)
        product_name = random.choice(list(PRODUCTS.keys()))
        prod = PRODUCTS[product_name]
        qty = round(random.uniform(1, 100), 1)
        unit_price = round(random.uniform(*prod["price_range"]), 2)
        total = round(qty * unit_price, 2)
        status = random.choices(ORDER_STATUSES, weights=[10, 20, 15, 45, 10])[0]
        order_date = base_date + timedelta(days=random.randint(0, 500))
        lead_days = random.randint(15, 90)
        delivery_date = order_date + timedelta(days=lead_days)
        responsible = random.choice(RESPONSIBLE_PEOPLE)
        remark = random.choice(remarks_pool)

        ws.append([
            f"WO{2025000 + i}", customer_name, city, scale,
            product_name, prod["code"], prod["spec"],
            qty, prod["unit"], unit_price, total, status,
            order_date.strftime("%Y-%m-%d"),
            delivery_date.strftime("%Y-%m-%d"),
            responsible, remark,
        ])


def generate_inventory(wb: Workbook, seed: int = 42):
    """生成 10 种物料库存数据"""
    random.seed(seed)
    ws = wb.create_sheet("库存数据")
    headers = ["物料编码", "物料名称", "规格型号", "库存数量", "单位",
               "仓库", "安全库存", "最近入库日期", "供应商", "批次号", "状态"]
    ws.append(headers)

    suppliers = [
        "中石化", "中石油", "万华化学", "巴斯夫中国", "陶氏化学",
        "荣盛石化", "恒力石化", "卫星化学",
    ]
    base_date = datetime(2025, 1, 1)

    for code, name, spec, unit, base_qty, warehouse in MATERIALS:
        qty = round(base_qty * random.uniform(0.3, 1.5), 1)
        safety = round(base_qty * 0.3, 1)
        last_in = base_date + timedelta(days=random.randint(30, 500))
        supplier = random.choice(suppliers)
        batch = f"BIN{random.randint(20250001, 20259999)}"
        status = "正常" if qty > safety else "低于安全库存"

        ws.append([
            code, name, spec, qty, unit,
            warehouse, safety,
            last_in.strftime("%Y-%m-%d"),
            supplier, batch, status,
        ])


def generate_inspections(wb: Workbook, seed: int = 42):
    """生成 100 条质检记录"""
    random.seed(seed)
    ws = wb.create_sheet("质检数据")
    headers = ["批次号", "产品名称", "检验项目", "计量单位", "标准范围",
               "检验结果", "数值结果", "判定", "检验员", "检验部门",
               "资质等级", "检验日期", "备注"]
    ws.append(headers)

    base_date = datetime(2025, 1, 1)
    fail_remarks = ["指标偏高", "指标偏低", "外观异常", "取样不均匀", "设备校准偏差"]

    for i in range(1, 101):
        product = random.choice(list(INSPECTION_ITEMS.keys()))
        items = INSPECTION_ITEMS[product]
        item_name, unit, std_range, std_val = random.choice(items)

        # 85% 合格率
        is_pass = random.random() < 0.85
        if is_pass:
            # 合格: 数值在标准范围内波动 ±5%
            value = round(std_val * random.uniform(0.95, 1.05), 3)
            result_text = "合格"
            remark = ""
        else:
            # 不合格: 数值偏离标准 ±10-20%
            deviation = random.choice([-1, 1]) * random.uniform(0.10, 0.20)
            value = round(std_val * (1 + deviation), 3)
            result_text = "不合格"
            remark = random.choice(fail_remarks)

        inspector_name, dept, level = random.choice(INSPECTORS)
        insp_date = base_date + timedelta(days=random.randint(0, 500))

        ws.append([
            f"B{20250000 + i}", product, item_name, unit, std_range,
            result_text, value, "合格" if is_pass else "不合格",
            inspector_name, dept, level,
            insp_date.strftime("%Y-%m-%d"), remark,
        ])


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    wb = Workbook()
    generate_orders(wb)
    generate_inventory(wb)
    generate_inspections(wb)

    path = DATA_DIR / "订单数据.xlsx"
    wb.save(str(path))
    print(f"生成: {path}")

    # 分别保存为独立文件（方便单独查看）
    for sheet_name, filename in [("订单数据", "订单数据.xlsx"), ("库存数据", "库存数据.xlsx"), ("质检数据", "质检数据.xlsx")]:
        wb_single = Workbook()
        ws_src = wb[sheet_name]
        ws_dst = wb_single.active
        ws_dst.title = sheet_name
        for row in ws_src.iter_rows(values_only=True):
            ws_dst.append(list(row))
        path = DATA_DIR / filename
        wb_single.save(str(path))
        print(f"生成: {path}")

    print(f"完成。数据目录: {DATA_DIR}")


if __name__ == "__main__":
    main()