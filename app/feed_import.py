"""每日饲料表：只读取原始输入，不依赖 Excel 的公式缓存。"""
import calendar
import math
import re
from io import BytesIO

from openpyxl import load_workbook

from . import security, service


class FeedWorkbookDetected(ValueError):
    import_format='feed'

    def __init__(self):
        super().__init__('这是每日饲料表，请选择数据所属月份，预览后确认导入。')


def is_feed_workbook(wb):
    for name in ('1-15天','16-31天'):
        if name in wb.sheetnames:
            ws=wb[name]
            if '转化' in str(ws['B3'].value) and '初始' in str(ws['B4'].value):
                return True
    return False


def parse(data, year, month):
    security.validate_xlsx(data)
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise ValueError("请选择正确的年份和月份")
    try:
        wb = load_workbook(BytesIO(data), data_only=False)
    except Exception as exc:
        raise ValueError("无法读取文件，请上传 .xlsx 表格") from exc
    sheets, warnings = [], []
    last = calendar.monthrange(year, month)[1]
    for name in ("1-15天", "16-31天"):
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        ponds, cells, seen = [], [], set()

        def number(row, col):
            cell = ws.cell(row, col)
            v = cell.value
            if v is None or v == "":
                return None
            if isinstance(v, bool) or cell.data_type == "f":
                raise ValueError(f"{name}!{cell.coordinate} 应填写原始数字")
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{name}!{cell.coordinate} 不是数字")
            if not math.isfinite(v) or v < 0:
                raise ValueError(f"{name}!{cell.coordinate} 必须是非负数字")
            return v

        for col in range(3, min(ws.max_column, 256) + 1):
            raw = str(ws.cell(2, col).value or "").strip()
            if raw in ("", "#"):
                continue
            match = re.fullmatch(r"(\d+)\s*[#＃]?", raw)
            if not match:
                raise ValueError(f"{name} 第 {col} 列池号无法识别：{raw}")
            pond = f"{int(match[1])}#"
            if pond in seen:
                raise ValueError(f"{name} 池号重复：{pond}")
            seen.add(pond)
            rate, weight = number(3, col), number(4, col)
            if rate is not None and rate > 1:
                raise ValueError(f"{name} {pond} 转化率请填写小数，例如 0.85")
            ponds.append(dict(name=pond, conv_rate=rate, orig_weight=weight))
            for row in range(5, min(ws.max_row, 200) + 1, 4):
                day = ws.cell(row, 1).value
                if not isinstance(day, (int, float)) or int(day) != day:
                    continue
                day = int(day)
                if not ((1 <= day <= 15) if name == "1-15天" else (16 <= day <= 31)):
                    raise ValueError(f"{name} 日期位置不正确：{day}")
                for offset, shift in ((0, "早"), (1, "晚")):
                    value = number(row + offset, col)
                    if value is None:
                        continue
                    if day > last:
                        raise ValueError(f"所选{year}年{month}月没有{day}日，但「{name}」{ws.cell(row+offset,col).coordinate}有该日投喂数据。请核对数据所属月份，未导入任何数据。")
                    cells.append(dict(day=day, shift=shift, name=pond, value=value))
        # 模板只有默认转化率时，不创建空白期间。
        if cells or any(p["orig_weight"] is not None for p in ponds):
            sheets.append(dict(sheet=name, half="上" if name == "1-15天" else "下", ponds=ponds, cells=cells))
    if not sheets:
        raise ValueError("未找到可导入的数据，请检查「1-15天」「16-31天」及初始重量、投喂数字")
    warnings.append("空白格及空池号列跳过；尾数、伤苗和备注保留。重复导入会更新同日期、池号、班次的数据。")
    return dict(year=year, month=month, sheets=sheets, warnings=warnings)


def preview(c, plan):
    existing = {p["name"]: p for p in service.list_ponds(c)}
    added = set()
    summaries = []
    for sheet in plan["sheets"]:
        conflicts = 0
        for cell in sheet["cells"]:
            pond = existing.get(cell["name"])
            if pond and c.execute("SELECT 1 FROM daily WHERE year=? AND month=? AND day=? AND shift=? AND pond_id=? AND kind='feed'", (plan["year"], plan["month"], cell["day"], cell["shift"], pond["id"])).fetchone():
                conflicts += 1
        added.update(p["name"] for p in sheet["ponds"] if p["name"] not in existing)
        summaries.append(dict(sheet=sheet["sheet"], ponds=len(sheet["ponds"]), daily_cells=len(sheet["cells"]), total_feed=sum(v["value"] for v in sheet["cells"]), existing_cells=conflicts, sample=sheet["cells"][:6]))
    return dict(year=plan["year"], month=plan["month"], sheets=summaries, new_ponds=sorted(added), warnings=plan["warnings"])


def apply(c, plan):
    periods = []
    for sheet in plan["sheets"]:
        existing = {p["name"]: p for p in service.list_ponds(c)}
        ids = {}
        for p in sheet["ponds"]:
            if p["name"] not in existing:
                pid = c.execute("INSERT INTO ponds(name,category,sort) VALUES(?,'新',?)", (p["name"], int(p["name"][:-1]))).lastrowid
            else:
                pid = existing[p["name"]]["id"]
            ids[p["name"]] = pid
        pid = service.ensure_half(c, plan["year"], plan["month"], sheet["half"])
        periods.append(pid)
        for p in sheet["ponds"]:
            c.execute("INSERT OR IGNORE INTO entries(period_id,pond_id) VALUES(?,?)", (pid, ids[p["name"]]))
            for field in ("conv_rate", "orig_weight"):
                if p[field] is not None:
                    c.execute(f"UPDATE entries SET {field}=? WHERE period_id=? AND pond_id=?", (p[field], pid, ids[p["name"]]))
        service.save_daily(c, plan["year"], plan["month"], "feed", [dict(day=v["day"], shift=v["shift"], pond_id=ids[v["name"]], value=v["value"]) for v in sheet["cells"]])
    return dict(period_ids=periods, daily_cells=sum(len(s["cells"]) for s in plan["sheets"]))
