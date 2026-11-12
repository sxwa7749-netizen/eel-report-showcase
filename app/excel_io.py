"""导入原来的 Excel 报表。导出见 excel_export.py。"""
from io import BytesIO

from openpyxl import load_workbook

from . import security, service,feed_import
from .calc import CATEGORIES

# ---------------- 导入 ----------------

ENTRY_COLS = {  # 「数据录入」表的列 → 字段
    2: "feed_kg", 3: "conv_rate", 4: "orig_weight", 5: "injured",
    6: "orig_count", 7: "daily_feed",
}


def _n(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v.strip() and not v.startswith("="):
        try:
            return float(v.strip().rstrip("%")) / (100 if v.strip().endswith("%") else 1)
        except ValueError:
            return None
    return None


def import_workbook(c, data: bytes) -> dict:
    security.validate_xlsx(data)
    wb = load_workbook(BytesIO(data))
    if feed_import.is_feed_workbook(wb):
        raise feed_import.FeedWorkbookDetected()
    if "基础设置" not in wb.sheetnames or "数据录入" not in wb.sheetnames:
        raise ValueError("文件里找不到「基础设置」或「数据录入」工作表")
    s = wb["基础设置"]
    year, month = int(s["B3"].value), int(s["B4"].value)
    day_range = str(s["B5"].value or "").strip() or "全月"

    # 池号（第 9 行）与分类（第 10 行），从 B 列往右：示例是 B~Z 25 列，池多的厂有几列读几列
    pond_ids = []  # 按位置，空位为 None
    existing = {p["name"]: p for p in service.list_ponds(c)}
    last = max((col for col in range(2, s.max_column + 1) if str(s.cell(9, col).value or "").strip()), default=26)
    for col in range(2, max(27, last + 1)):
        name = s.cell(9, col).value
        cat = s.cell(10, col).value
        if name is None or str(name).strip() == "":
            pond_ids.append(None)
            continue
        name = str(name).strip()
        cat = cat if cat in CATEGORIES else "新"
        if name in existing:
            pid = existing[name]["id"]
            c.execute("UPDATE ponds SET category=?, active=1, sort=? WHERE id=?", (cat, col - 1, pid))
        else:
            pid = c.execute(
                "INSERT INTO ponds(name, category, sort) VALUES(?,?,?)", (name, cat, col - 1)
            ).lastrowid
            existing[name] = {"id": pid}
        pond_ids.append((pid, cat))

    r = c.execute(
        "SELECT id FROM periods WHERE year=? AND month=? AND day_range=?", (year, month, day_range)
    ).fetchone()
    replaced = bool(r)
    if r:
        period_id = r["id"]
        c.execute("DELETE FROM entries WHERE period_id=?", (period_id,))
    else:
        period_id = c.execute(
            "INSERT INTO periods(year, month, day_range) VALUES(?,?,?)", (year, month, day_range)
        ).lastrowid

    d = wb["数据录入"]
    rows = []
    for i, info in enumerate(pond_ids):
        if not info:
            continue
        excel_row = i + 2
        row = {"pond_id": info[0], "category": info[1]}
        for col, field in ENTRY_COLS.items():
            row[field] = _n(d.cell(excel_row, col).value)
        h = d.cell(excel_row, 8).value  # 现尾数：手填数字才作为覆盖值
        row["cur_count_override"] = _n(h)
        note = d.cell(excel_row, 11).value
        row["note"] = str(note) if note not in (None, "") else None
        rows.append(row)
    service.save_entries(c, period_id, rows)

    daily_count = 0
    for sheet, kind in (("投食计划表", "feed"), ("伤苗", "injury")):
        if sheet in wb.sheetnames:
            daily_count += _import_daily(c, wb[sheet], year, month, kind, pond_ids)

    return {
        "period_id": period_id,
        "label": f"{year}年{month}月（{day_range}）",
        "replaced": replaced,
        "ponds": sum(1 for p in pond_ids if p),
        "daily_cells": daily_count,
    }


def _import_daily(c, ws, year, month, kind, pond_ids):
    """每天两行：带日期数字的一行是「早」，紧接着的一行是「晚」。池号从 C 列开始。"""
    cells, day, next_is_evening = [], None, False
    for r in range(1, ws.max_row + 1):
        b = ws.cell(r, 2).value
        if isinstance(b, (int, float)) and 1 <= int(b) <= 31:
            day, shift, next_is_evening = int(b), "早", True
        elif next_is_evening and day and ws.cell(r, 1).value in ("早", "晚"):
            shift, next_is_evening = "晚", False
        else:
            next_is_evening = False
            continue
        for i, info in enumerate(pond_ids):
            if not info:
                continue
            v = _n(ws.cell(r, 3 + i).value)
            if v is not None:
                cells.append({"day": day, "shift": shift, "pond_id": info[0], "value": v})
    service.save_daily(c, year, month, kind, cells)
    return len(cells)
