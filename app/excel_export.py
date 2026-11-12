"""把网页数据导出成 Excel，版面按 A4 纸打印设计：字号 11~12，打印基本不缩小。

- 报表（横版）：A4 横向，按纸宽一页约 14 口池、字号不缩小，放不下的平均排到后面几页；第一页上方是汇总。
- 报表竖版：A4 纵向，每池一行，放不下的排到第二页（表头重复）。
- 只列有数据的池。
- 投喂 / 伤苗月表：A4 横向，1-15 号一页，16 号到月底一页，每页都有表头。
"""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.pagebreak import Break

from . import farms, report_import, service
from .calc import CATEGORIES, compute_totals

FONT = "微软雅黑"
THIN = Side(style="thin", color="7F8C99")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="DCE6F0")
SUB_FILL = PatternFill("solid", fgColor="F0F3F7")
TOTAL_FILL = PatternFill("solid", fgColor="C9D8E8")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

KG = "#,##0.0"
KG0 = "#,##0"
INT = "#,##0"
P1 = "0.0"
PCT2 = "0.00%"


def PCT1(v):
    """转化率按实际值显示，不四舍五入成整数：0.85 → 85%，0.855 → 85.5%，0.8525 → 85.25%"""
    if v is None:
        return "0%"
    x = round(v * 100, 6)
    if abs(x - round(x)) < 1e-6:
        return "0%"
    if abs(x * 10 - round(x * 10)) < 1e-6:
        return "0.0%"
    return "0.00%"


def _fmt(fmt, v):
    """格式可以是固定字符串，也可以按值决定（如转化率）"""
    return fmt(v) if callable(fmt) else fmt



def _cell(ws, r, c, v, fmt=None, *, size=12, bold=False, fill=None, align=None):
    cell = ws.cell(r, c, v)
    if isinstance(v, str):
        cell.data_type = 's'  # 用户文本不作为Excel公式执行。
    cell.border = BORDER
    cell.font = Font(name=FONT, size=size, bold=bold)
    cell.alignment = align or (RIGHT if isinstance(v, (int, float)) else CENTER)
    if fmt:
        cell.number_format = _fmt(fmt, v)
    if fill:
        cell.fill = fill
    return cell


def _merge(ws, r1, c1, r2, c2):
    ws.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)
    # 合并区域的边框要逐格补上，打印才完整
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            ws.cell(r, c).border = BORDER


def _title(ws, row, text, width, size=16):
    c = ws.cell(row, 1, text)
    c.font = Font(name=FONT, size=size, bold=True)
    c.alignment = CENTER
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    ws.row_dimensions[row].height = 32


def _print_setup(ws, landscape, repeat_rows=None):
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1   # 宽度压到一页（版面本来就按一页宽设计，基本不缩）
    ws.page_setup.fitToHeight = 0  # 高度不限，按分页符分页
    ws.page_margins = PageMargins(left=0.3, right=0.3, top=0.4, bottom=0.45, header=0.15, footer=0.2)
    ws.print_options.horizontalCentered = True
    if repeat_rows:
        ws.print_title_rows = repeat_rows
    ws.oddFooter.center.text = "第 &P 页，共 &N 页"
    ws.oddFooter.center.size = 10


def _widths(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def has_data(r):
    """这口池这一期有没有任何数据（只有沿用来的转化率不算）"""
    calc = r["calc"]
    nums = (calc.get("cur_weight"), calc.get("cur_count"), calc.get("feed_kg"), calc.get("injured"), r.get("daily_feed"))
    return any(v for v in nums) or bool((r.get("note") or "").strip())


def export_period(c, period_id) -> tuple[bytes, str]:
    rep = service.period_report(c, period_id)
    p = rep["period"]
    # 报表只列有数据的池（没重量、没尾数、没投料、没伤苗、没当日投喂、没备注的空池不体现），汇总也按这些池算
    rows = [r for r in rep["rows"] if has_data(r)]
    totals = compute_totals(rows)
    wb = Workbook()
    _sheet_horizontal(wb.active, p, rows, totals)
    _sheet_vertical(wb.create_sheet("报表竖版"), p, rows, totals)
    for kind, title in (("feed", "投喂月表"), ("injury", "伤苗月表")):
        _sheet_daily(wb.create_sheet(title), c, p["year"], p["month"], kind)
    # 打印页保持原样；隐藏的原始数据页保证新导出文件可以完整回导。
    raw=wb.create_sheet(report_import.BACKUP_SHEET)
    raw.append(['EEL-REPORT-BACKUP',1])
    raw.append(['farm',farms.current.get()])
    raw.append(['year',p['year']])
    raw.append(['month',p['month']])
    raw.append(['day_range',p['day_range']])
    raw.append(['说明','网页报表导入使用，请保留此页；期初值按导出时实际值保存。'])
    raw.append([])
    raw.append(report_import.FIELDS)
    for row in rep['rows']:
        snapshot=dict(row)
        for field in service.INHERIT_FIELDS:
            snapshot[field]=row['calc'].get(field)
        raw.append([snapshot.get(field) for field in report_import.FIELDS])
    daily=wb.create_sheet(report_import.DAILY_SHEET)
    daily.append(['day','shift','name','kind','value'])
    for cell in c.execute('SELECT d.day,d.shift,p.name,d.kind,d.value FROM daily d JOIN ponds p ON p.id=d.pond_id WHERE d.year=? AND d.month=? ORDER BY d.day,d.shift,p.sort,p.id,d.kind',(p['year'],p['month'])):
        daily.append(list(cell))
    for sheet in (raw,daily):
        for line in sheet:
            for cell in line:
                if isinstance(cell.value,str):
                    cell.data_type='s'
        sheet.sheet_state='hidden'
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"{farms.title()}报表_{p['year']}年{p['month']}月_{p['day_range']}.xlsx"


# ---------------- 汇总表（横版、竖版共用） ----------------

SUMMARY_HEADS = ["池数", "投料\nkg", "增重\nkg", "原重\nkg", "现有重量\nkg", "伤苗\n尾", "现尾数\n尾", "平均\nP数"]


def _summary(ws, start, totals, spans):
    """spans：9 个 (起始列, 结束列)，依次是 分类 + 8 个数字；跨多列的会合并。返回下一可用行号。"""
    def put(r, k, v, fmt=None, **kw):
        a, b = spans[k]
        _cell(ws, r, a, v, fmt, **kw)
        if b > a:
            _merge(ws, r, a, r, b)

    put(start, 0, "分类", bold=True, fill=HEAD_FILL)
    for j, h in enumerate(SUMMARY_HEADS):
        put(start, j + 1, h, bold=True, fill=HEAD_FILL, size=11)
    ws.row_dimensions[start].height = 34
    for i, name in enumerate(["总计", *CATEGORIES]):
        r = start + 1 + i
        t = totals[name]
        fill = TOTAL_FILL if name == "总计" else None
        put(r, 0, name, bold=True, fill=fill or SUB_FILL)
        vals = [t["ponds"], t["feed_kg"], t["gain"], t["orig_weight"], t["cur_weight"],
                t["injured"], t["cur_count"], t["avg_p"]]
        fmts = [INT, KG0, KG0, KG0, KG0, INT, INT, P1]
        for j, v in enumerate(vals):
            put(r, j + 1, v, fmts[j], bold=(name == "总计"), fill=fill)
        ws.row_dimensions[r].height = 22
    return start + 5


# ---------------- 报表（横版） ----------------

# 横版一页放下全部池号：重量取整、点数 1 位小数，尽量省宽度；整页缩放到一张 A4 横向
ONE_PAGE_METRICS = [
    ("投料\n(kg)", lambda r: r["calc"]["feed_kg"], KG0),
    ("转化率", lambda r: r["calc"]["conv_rate"], PCT1),
    ("增重\n(kg)", lambda r: r["calc"]["gain"], KG0),
    ("原重量\n(kg)", lambda r: r["calc"]["orig_weight"], KG0),
    ("现有重量\n(kg)", lambda r: r["calc"]["cur_weight"], KG0),
    ("伤苗\n(尾)", lambda r: r["calc"]["injured"], INT),
    ("现尾数\n(尾)", lambda r: r["calc"]["cur_count"], INT),
    ("现有\nP数", lambda r: r["calc"]["p"], P1),
    ("当日投喂\n(kg)", lambda r: r["daily_feed"], "0.0"),
    ("点数", lambda r: r["calc"]["points"], "0.0%"),
]


def _split(total, parts):
    """把 total 列尽量平均分成 parts 段，返回每段 (起, 止) 的相对列号（从 0 开始）。"""
    base, extra = divmod(total, parts)
    out, c = [], 0
    for i in range(parts):
        w = base + (1 if i >= parts - extra else 0)
        out.append((c, c + w - 1))
        c += w
    return out


HORIZONTAL_PER_PAGE = 25  # 横版一页最多放几个池（实际还要看 A4 宽度放得下几口，见 _per_page）

# A4 横向可打印区域（左右各 0.2 英寸、上 0.3、下 0.35 英寸页边距），单位：像素（列宽）/ 磅（行高）
A4_LANDSCAPE_W_PX = int((297 - 2 * 0.2 * 25.4) / 25.4 * 96)          # ≈ 1084
A4_LANDSCAPE_H_PT = (210 / 25.4 - 0.3 - 0.35) * 72                   # ≈ 548
LABEL_W = 8.6


def _px(w):
    """Excel 列宽（字符数）→ 屏幕像素，和 Excel / WPS 的换算一致（默认字体最大数字宽 7 像素）"""
    return int(w * 7 + 5)


def _per_page(col_w):
    """按 A4 横向宽度，一页能放几口池（不缩小字号）"""
    return max(1, min(HORIZONTAL_PER_PAGE, (A4_LANDSCAPE_W_PX - _px(LABEL_W)) // _px(col_w)))


def _text_width(row):
    """这个池各项数字里最长的显示宽度（含千位分隔符），用来定列宽"""
    longest = 0
    for _, get, fmt in ONE_PAGE_METRICS:
        value = get(row)
        if value is None:
            continue
        fmt = _fmt(fmt, value)
        if "%" in fmt:
            decimals = len(fmt.split(".")[1]) - 1 if "." in fmt else 0
            text = format(value, f".{decimals}%")
        elif fmt in ("0.0", P1):
            text = format(value, ".1f")
        else:
            text = format(value, ",.0f")
        longest = max(longest, len(text))
    return max(9.5, longest + 2.5)


def _sheet_horizontal(ws, p, rows, totals):
    """按 A4 横向实际宽度分页：字号不缩小，一页放得下几口池就放几口（约 14 口），
    放不下的平均分到后面几页。第一页上面是汇总，每页都有标题和表头。
    用固定缩放比例而不是「缩放到一页」，这样 Excel 和 WPS 都会按分页符分页。"""
    ws.title = "报表"
    col_w = max([9.5] + [_text_width(r) for r in rows])  # 所有池用同一列宽，各页一样宽
    per_page = _per_page(col_w)
    pages = max(1, -(-len(rows) // per_page))
    blocks = [rows[a:b + 1] for a, b in _split(len(rows), pages)] if rows else [[]]
    n = max(max(len(b) for b in blocks), 8)
    width = 1 + n
    _widths(ws, [LABEL_W] + [col_w] * n)
    title = f"{farms.title()} {p['year']}年{p['month']}月（{p['day_range']}）号报表"
    _title(ws, 1, title + (f"（第 1 页，共 {pages} 页）" if pages > 1 else ""), width, size=18)
    ws.row_dimensions[1].height = 34
    page_h = [34 + 15]  # 每页内容高度（磅），用来算缩放比例

    # 汇总：分类在 A 列，8 个数字平均分到池号列上（每个横跨约 2 列，字可以大一些）
    spans = [(1, 1)] + [(2 + a, 2 + b) for a, b in _split(n, 8)]
    r = _summary(ws, 3, totals, spans)
    for rr in range(3, r):
        ws.row_dimensions[rr].height = 24 if rr > 3 else 32
    page_h[0] += 32 + 4 * 24 + 15
    r += 1

    for k, block in enumerate(blocks):
        if k:  # 第二块起另起一页，页头再写一次标题
            ws.row_breaks.append(Break(id=r - 1))
            _title(ws, r, f"{title}（第 {k + 1} 页，共 {pages} 页）", width, size=14)
            ws.row_dimensions[r].height = 28
            page_h.append(28 + 15)
            r += 2
        _cell(ws, r, 1, "项目", bold=True, fill=HEAD_FILL, size=11)
        for j, row in enumerate(block):  # 这一页池少时，右边多出的列留白，不画框
            _cell(ws, r, 2 + j, f"{row['name']}\n{row['category']}", bold=True, fill=HEAD_FILL, size=11)
        ws.row_dimensions[r].height = 34
        r += 1
        for label, get, fmt in ONE_PAGE_METRICS:
            _cell(ws, r, 1, label, bold=True, fill=SUB_FILL, size=10)
            for j, row in enumerate(block):
                _cell(ws, r, 2 + j, get(row), fmt, size=11)
            ws.row_dimensions[r].height = 30
            r += 1
        page_h[-1] += 34 + 30 * len(ONE_PAGE_METRICS)

    _print_setup(ws, landscape=True)
    ws.page_margins = PageMargins(left=0.2, right=0.2, top=0.3, bottom=0.35, header=0.1, footer=0.15)
    # 不用「缩放到一页」（Excel 会忽略分页符），改成固定比例：宽、高都放得下，最多 100%
    ws.sheet_properties.pageSetUpPr.fitToPage = False
    used_w = _px(LABEL_W) + n * _px(col_w)
    scale = min(100, A4_LANDSCAPE_W_PX * 100 // used_w, int(A4_LANDSCAPE_H_PT * 100 // max(page_h)))
    ws.page_setup.scale = max(10, scale)


# ---------------- 报表竖版 ----------------

VERTICAL_COLS = [
    ("池号", 7, lambda r: r["name"], None),
    ("分类", 6, lambda r: r["category"], None),
    ("投料\nkg", 10, lambda r: r["calc"]["feed_kg"], KG),
    ("转化率", 7.5, lambda r: r["calc"]["conv_rate"], PCT1),
    ("增重\nkg", 9.5, lambda r: r["calc"]["gain"], KG),
    ("原重\nkg", 10, lambda r: r["calc"]["orig_weight"], KG),
    ("现有重量\nkg", 10.5, lambda r: r["calc"]["cur_weight"], KG),
    ("伤苗\n尾", 7, lambda r: r["calc"]["injured"], INT),
    ("现尾数\n尾", 10, lambda r: r["calc"]["cur_count"], INT),
    ("P数", 7.5, lambda r: r["calc"]["p"], P1),
    ("当日投喂\nkg", 9.5, lambda r: r["daily_feed"], KG),
    ("点数", 8, lambda r: r["calc"]["points"], PCT2),
]


def _sheet_vertical(ws, p, rows, totals):
    n = len(VERTICAL_COLS)
    _widths(ws, [w for _, w, _, _ in VERTICAL_COLS])
    _title(ws, 1, f"{farms.title()}报表 {p['year']}年{p['month']}月（{p['day_range']}）", n)
    for j, (h, _, _, _) in enumerate(VERTICAL_COLS):
        _cell(ws, 2, 1 + j, h, bold=True, fill=HEAD_FILL, size=11)
    ws.row_dimensions[2].height = 34
    r = 3
    for row in rows:
        for j, (_, _, get, fmt) in enumerate(VERTICAL_COLS):
            v = get(row)
            fill = SUB_FILL if j < 2 else None
            _cell(ws, r, 1 + j, v, fmt, bold=(j == 0), fill=fill)
        ws.row_dimensions[r].height = 21
        r += 1
    # 竖版各列宽度不同，汇总的大数字要放在够宽的列里：增重、现尾数、P 数各占两列
    # 分类|池数|投料|增重|原重|现有重量|伤苗|现尾数|P数 → 列 1|2|3|4-5|6|7|8|9-10|11-12
    spans = [(1, 1), (2, 2), (3, 3), (4, 5), (6, 6), (7, 7), (8, 8), (9, 10), (11, 12)]
    r = _summary(ws, r + 1, totals, spans) + 1

    notes = [f"{row['name']}：{row['note']}" for row in rows if row.get("note")]
    if notes:
        c = ws.cell(r, 1, "备注　" + "；".join(notes))
        c.font = Font(name=FONT, size=11)
        c.alignment = LEFT
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=n)
        ws.row_dimensions[r].height = max(22, 18 * (len("；".join(notes)) // 70 + 1))
    _print_setup(ws, landscape=False, repeat_rows="2:2")


# ---------------- 投喂 / 伤苗月表 ----------------
# 投喂量带小数（如 124.5），25 个池挤一行会被缩成 124 甚至 ###，所以每页只放 13 个池；
# 伤苗是小整数，25 个池一页放得下。每组池号再按 1-15 号、16 号到月底分两页。

DAILY_BLOCK = {"feed": 13, "injury": 26}
PAGE_PX = 1060  # A4 横向、左右各 0.3 英寸边距时，一页大约能放的列宽像素


def _sheet_daily(ws, c, year, month, kind):
    g = service.daily_grid(c, year, month, kind)
    ponds, vals = g["ponds"], g["values"]
    size = DAILY_BLOCK[kind]
    blocks = [ponds[i:i + size] for i in range(0, len(ponds), size)] or [[]]
    many = len(blocks) > 1
    per_page = max(len(b) for b in blocks) or 1

    cell_fmt = "#,##0.0" if kind == "feed" else "0"
    sum_fmt = "#,##0.0" if kind == "feed" else "#,##0"
    font = 12 if kind == "feed" else 11
    total_w = 11.5 if kind == "feed" else 7.5
    used = (5.6 * 7 + 5) + (4.6 * 7 + 5) + (total_w * 7 + 5)
    pond_w = max(4.6, min(10.5, ((PAGE_PX - used) / per_page - 5) / 7))
    _widths(ws, [5.6, 4.6] + [pond_w] * per_page + [total_w])
    width = per_page + 3
    unit = "投喂量（kg）" if kind == "feed" else "伤苗（尾）"
    total_head = "全场\n合计" if many else "合计"

    def pad(row, block, fill=None):
        """池号不满一页时，空列也画上边框，合计列紧跟着不留空。"""
        for j in range(len(block), per_page):
            _cell(ws, row, 3 + j, None, fill=fill)

    def all_ponds_sum(day, shift):
        s = [vals.get(f"{day}|{shift}|{p['id']}") for p in ponds]
        s = [v for v in s if v is not None]
        return sum(s) if s else None

    r = 1
    for bi, block in enumerate(blocks):
        month_tot = [0.0] * len(block)
        month_all = 0.0
        for h, (a, b) in enumerate([(1, 15), (16, g["days"])]):
            if r > 1:
                ws.row_breaks.append(Break(id=r - 1))
            if h == 0:
                label = f"（{block[0]['name']}～{block[-1]['name']}）" if many and block else ""
                _title(ws, r, f"{farms.title()}每日{unit}  {year}年{month}月{label}", width, size=15)
                r += 1
            # 表头
            _cell(ws, r, 1, "日期", bold=True, fill=HEAD_FILL, size=10)
            _cell(ws, r, 2, "班次", bold=True, fill=HEAD_FILL, size=10)
            for j, p in enumerate(block):
                _cell(ws, r, 3 + j, p["name"], bold=True, fill=HEAD_FILL, size=font)
            pad(r, block, HEAD_FILL)
            _cell(ws, r, width, total_head, bold=True, fill=HEAD_FILL, size=10)
            ws.row_dimensions[r].height = 32 if many else 22
            r += 1

            half_tot = [0.0] * len(block)
            half_all = 0.0
            for day in range(a, b + 1):
                for k, shift in enumerate(service.SHIFTS):
                    if k == 0:
                        _cell(ws, r, 1, day, size=11, bold=True, align=CENTER)
                        _merge(ws, r, 1, r + 1, 1)
                    _cell(ws, r, 2, shift, size=10, fill=SUB_FILL)
                    for j, p in enumerate(block):
                        v = vals.get(f"{day}|{shift}|{p['id']}")
                        _cell(ws, r, 3 + j, v, cell_fmt, size=font)
                        if v is not None:
                            half_tot[j] += v
                    pad(r, block)
                    row_all = all_ponds_sum(day, shift)
                    _cell(ws, r, width, row_all, sum_fmt, size=11, bold=True)
                    half_all += row_all or 0
                    ws.row_dimensions[r].height = 15.5
                    r += 1
            _cell(ws, r, 1, f"{a}-{b}小计", size=10, bold=True, fill=TOTAL_FILL)
            _merge(ws, r, 1, r, 2)
            for j, v in enumerate(half_tot):
                _cell(ws, r, 3 + j, v or None, sum_fmt, size=font, bold=True, fill=TOTAL_FILL)
                month_tot[j] += v
            pad(r, block, TOTAL_FILL)
            _cell(ws, r, width, half_all or None, sum_fmt, size=11, bold=True, fill=TOTAL_FILL)
            month_all += half_all
            ws.row_dimensions[r].height = 20
            r += 1

        _cell(ws, r, 1, "本月合计", size=10, bold=True, fill=HEAD_FILL)
        _merge(ws, r, 1, r, 2)
        for j, v in enumerate(month_tot):
            _cell(ws, r, 3 + j, v or None, sum_fmt, size=font, bold=True, fill=HEAD_FILL)
        pad(r, block, HEAD_FILL)
        _cell(ws, r, width, month_all or None, sum_fmt, size=11, bold=True, fill=HEAD_FILL)
        ws.row_dimensions[r].height = 20
        r += 1
    _print_setup(ws, landscape=True)
