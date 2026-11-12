"""数据读写：池号、报表期间、每期录入、每日投食/伤苗。"""
import calendar
import math
import json
from datetime import datetime, timedelta, timezone

from .calc import CATEGORIES, INPUT_FIELDS, compute_row, compute_totals

DAILY_KINDS = {"feed": "投食", "injury": "伤苗"}
SHIFTS = ["早", "晚"]


def num(v):
    if v is None or v == "":
        return None
    try:
        value = float(v)
        if not math.isfinite(value):
            raise ValueError()
        return value
    except (TypeError, ValueError):
        raise ValueError(f"不是有效数字：{v}")


# ---------- 池号 ----------

def list_ponds(c, include_inactive=True):
    sql = "SELECT * FROM ponds" + ("" if include_inactive else " WHERE active=1")
    return [dict(r) for r in c.execute(sql + " ORDER BY sort, id")]


def setup_ponds(c, count, category="新"):
    """新厂第一次使用：生成 1#～N#。只有一个池号都没有时才能用，避免误操作覆盖。"""
    try:
        n = int(count)
    except (TypeError, ValueError):
        raise ValueError("请填池子数量")
    if not 1 <= n <= 300:
        raise ValueError("池子数量要在 1～300 之间")
    if c.execute("SELECT COUNT(*) FROM ponds").fetchone()[0]:
        raise ValueError("这个厂已经有池号了，请到「池号管理」里修改")
    cat = category if category in CATEGORIES else "新"
    c.executemany("INSERT INTO ponds(name, category, sort) VALUES(?,?,?)", [(f"{i}#", cat, i) for i in range(1, n + 1)])
    return list_ponds(c)


def save_ponds(c, ponds: list[dict]):
    names = [p["name"].strip() for p in ponds]
    if any(not n for n in names):
        raise ValueError("池号不能为空")
    if len(set(names)) != len(names):
        raise ValueError("池号有重复")
    old_names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM ponds")}
    # 先把名字改成临时值，避免改名互换时撞 UNIQUE
    c.execute("UPDATE ponds SET name = '__tmp__' || id")
    for i, p in enumerate(ponds, start=1):
        cat = p.get("category") if p.get("category") in CATEGORIES else "新"
        active = 1 if p.get("active", True) else 0
        if p.get("id"):
            c.execute(
                "UPDATE ponds SET name=?, category=?, active=?, sort=? WHERE id=?",
                (p["name"].strip(), cat, active, i, p["id"]),
            )
        else:
            c.execute(
                "INSERT INTO ponds(name, category, active, sort) VALUES(?,?,?,?)",
                (p["name"].strip(), cat, active, i),
            )
    # 列表里没有的池号：有数据就停用，没数据就删除
    keep = [p["id"] for p in ponds if p.get("id")]
    for r in c.execute("SELECT id, name FROM ponds WHERE name LIKE '__tmp__%'").fetchall():
        if r["id"] in keep:
            continue
        used = c.execute(
            "SELECT 1 FROM entries WHERE pond_id=? UNION SELECT 1 FROM daily WHERE pond_id=? LIMIT 1",
            (r["id"], r["id"]),
        ).fetchone()
        if used:
            name = old_names[r["id"]]
            if name in names:  # 名字已被新池号占用
                name = f"{name}(旧{r['id']})"
            c.execute("UPDATE ponds SET name=?, active=0 WHERE id=?", (name, r["id"]))
        else:
            c.execute("DELETE FROM ponds WHERE id=?", (r["id"],))


# ---------- 报表期间 ----------

def list_periods(c):
    return [
        dict(r)
        for r in c.execute("SELECT * FROM periods ORDER BY year DESC, month DESC, day_range DESC")
    ]


def get_period(c, pid):
    r = c.execute("SELECT * FROM periods WHERE id=?", (pid,)).fetchone()
    if not r:
        raise LookupError("报表期间不存在")
    return dict(r)


def create_period(c, year, month, day_range, carry_from=None):
    day_range = str(day_range).strip()
    if not (2000 <= int(year) <= 2100 and 1 <= int(month) <= 12 and day_range):
        raise ValueError("年份、月份或日期范围不正确")
    if c.execute(
        "SELECT 1 FROM periods WHERE year=? AND month=? AND day_range=?", (year, month, day_range)
    ).fetchone():
        raise ValueError(f"{year}年{month}月（{day_range}）已经存在")
    # 新建的期报一律实时沿用上一期（空着的格子）；导入 Excel 建的期报不走这里，不沿用
    pid = c.execute(
        "INSERT INTO periods(year, month, day_range, carry_live) VALUES(?,?,?,1)", (year, month, day_range)
    ).lastrowid

    prev = {}
    if carry_from:
        for row in period_rows(c, carry_from):
            prev[row["pond_id"]] = row
    for p in list_ponds(c, include_inactive=False):
        r = prev.get(p["id"])
        # 只带入分类。转化率、原重量、原尾数留空 → 计算时实时沿用上一期（见 period_rows），
        # 上一期之后再改，这一期也跟着变；需要时可以手填覆盖。
        c.execute(
            "INSERT INTO entries(period_id, pond_id, category) VALUES(?,?,?)",
            (pid, p["id"], r["category"] if r else p["category"]),
        )
    return pid


def release_carried(c):
    """一次性转换：以前新建一期时把上一期的数复制成了固定值。和上一期现在的数相同的，
    改回留空（实时沿用上一期）；手填过、和上一期不同的保持不变。返回改了几个格子。"""
    # 判断「是带入的」：这一期填了原重量的池里，至少 80% 和上一期现在的现有重量一模一样。
    # 导入或手填的期报（比如和一年前的旧期报相邻）对不上，保持原样、也不沿用。
    changed = 0
    periods = sorted((dict(r) for r in c.execute("SELECT * FROM periods WHERE carry_live=0")), key=_period_key)
    for p in periods:
        prev = previous_period(c, p["year"], p["month"], day_span(p)[0])
        if not prev:
            continue
        prev_rows = {r["pond_id"]: r for r in period_rows(c, prev["id"])}
        entries = c.execute("SELECT pond_id, conv_rate, orig_weight, orig_count FROM entries WHERE period_id=?", (p["id"],)).fetchall()
        now = {}
        for e in entries:
            pr = prev_rows.get(e["pond_id"])
            if pr:
                now[e["pond_id"]] = {"conv_rate": pr["calc"]["conv_rate"], "orig_weight": pr["calc"]["cur_weight"], "orig_count": pr["calc"]["cur_count"]}
        same = lambda e, f: e[f] is not None and now.get(e["pond_id"], {}).get(f) is not None and abs(e[f] - now[e["pond_id"]][f]) < 1e-6
        weighed = [e for e in entries if e["orig_weight"] is not None]
        if not weighed or sum(same(e, "orig_weight") for e in weighed) < 0.8 * len(weighed):
            continue
        c.execute("UPDATE periods SET carry_live=1 WHERE id=?", (p["id"],))
        for e in entries:
            for f in INHERIT_FIELDS:
                if same(e, f):
                    c.execute(f"UPDATE entries SET {f}=NULL WHERE period_id=? AND pond_id=?", (p["id"], e["pond_id"]))
                    changed += 1
    return changed


def ensure_current_half(c, now=None):
    """到了 1 号、16 号自动开始新的半月期报（只在已经有过期报、也有池号的厂）。
    新一期的转化率、原重量、原尾数实时沿用上一期，不用管理员点「开始」。返回期报 id 或 None。"""
    now = now or datetime.now(CN_TZ)
    half = "上" if now.day <= 15 else "下"
    p = find_half(c, now.year, now.month, half)
    if p:
        return p["id"]
    if not c.execute("SELECT 1 FROM ponds WHERE active=1 LIMIT 1").fetchone():
        return None
    if not previous_period(c, now.year, now.month, 1 if half == "上" else 16):
        return None  # 一期都没有的新厂：第一期要管理员自己开始、手填期初数
    return ensure_half(c, now.year, now.month, half)


def delete_period(c, pid):
    period=get_period(c,pid)
    entries=[dict(r) for r in c.execute('SELECT e.*,p.name FROM entries e JOIN ponds p ON p.id=e.pond_id WHERE e.period_id=?',(pid,))]
    c.execute('INSERT INTO deleted_periods(payload) VALUES(?)',(json.dumps(dict(period=period,entries=entries),ensure_ascii=False),))
    c.execute("DELETE FROM periods WHERE id=?", (pid,))


def deleted_periods(c):
    result=[]
    for row in c.execute('SELECT * FROM deleted_periods WHERE restored_at IS NULL ORDER BY id DESC'):
        payload=json.loads(row['payload']);p=payload['period']
        result.append(dict(id=row['id'],label=f"{p['year']}年{p['month']}月（{p['day_range']}）",ponds=len(payload['entries']),deleted_at=row['deleted_at']))
    return result


def restore_period(c,tid):
    trash=c.execute('SELECT * FROM deleted_periods WHERE id=? AND restored_at IS NULL',(tid,)).fetchone()
    if not trash:
        raise LookupError('这条删除记录不存在或已经恢复')
    payload=json.loads(trash['payload']);p=payload['period']
    existing=c.execute('SELECT id FROM periods WHERE year=? AND month=? AND day_range=?',(p['year'],p['month'],p['day_range'])).fetchone()
    pid=existing[0] if existing else c.execute('INSERT INTO periods(year,month,day_range,carry_live) VALUES(?,?,?,?)',(p['year'],p['month'],p['day_range'],p.get('carry_live',0))).lastrowid
    for row in payload['entries']:
        pond=c.execute('SELECT id FROM ponds WHERE name=?',(row['name'],)).fetchone()
        pond_id=pond[0] if pond else c.execute("INSERT INTO ponds(name,category) VALUES(?,?)",(row['name'],row.get('category') or '新')).lastrowid
        c.execute('INSERT OR IGNORE INTO entries(period_id,pond_id,category) VALUES(?,?,?)',(pid,pond_id,row.get('category')))
        now=c.execute('SELECT * FROM entries WHERE period_id=? AND pond_id=?',(pid,pond_id)).fetchone()
        for field in [*INPUT_FIELDS,'note']:
            if row.get(field) is not None and (now[field] is None or now[field]==''):
                c.execute(f'UPDATE entries SET {field}=? WHERE period_id=? AND pond_id=?',(row[field],pid,pond_id))
    c.execute('UPDATE deleted_periods SET restored_at=CURRENT_TIMESTAMP WHERE id=?',(tid,))
    return dict(period_id=pid)


# ---------- 半月期间（上半月 1-15，下半月 16-月底），按日期自动对应 ----------

HALVES = {"上": "上半月", "下": "下半月"}


def half_range(year, month, half):
    last = calendar.monthrange(year, month)[1]
    return "1-15" if half == "上" else f"16-{last}"


def half_of_day(day):
    return "上" if day <= 15 else "下"


def _period_key(p):
    return (p["year"], p["month"], day_span(p)[0])


def find_half(c, year, month, half):
    """找这个半月对应的期间；兼容以前导入的写法（如 16-30 写成别的月底）。"""
    want_start = 1 if half == "上" else 16
    for r in c.execute("SELECT * FROM periods WHERE year=? AND month=?", (year, month)):
        p = dict(r)
        a, b = day_span(p)
        if a == want_start and (half == "下" or b <= 15):
            return p
    return None


def previous_period(c, year, month, start_day):
    """日期在它之前、最近的一期（用来带入原重量、原尾数）。"""
    key = (year, month, start_day)
    best = None
    for r in c.execute("SELECT * FROM periods"):
        p = dict(r)
        k = _period_key(p)
        if k < key and (best is None or k > _period_key(best)):
            best = p
    return best


def half_info(c, year, month, half):
    if half not in HALVES or not (1 <= month <= 12):
        raise ValueError("月份或上下半月不正确")
    p = find_half(c, year, month, half)
    start = 1 if half == "上" else 16
    prev = previous_period(c, year, month, start)
    return {
        "year": year, "month": month, "half": half,
        "day_range": p["day_range"] if p else half_range(year, month, half),
        "id": p["id"] if p else None,
        "prev_label": f"{prev['year']}年{prev['month']}月（{prev['day_range']}）" if prev else None,
    }


def ensure_half(c, year, month, half):
    info = half_info(c, year, month, half)
    if info["id"]:
        return info["id"]
    start = 1 if half == "上" else 16
    prev = previous_period(c, year, month, start)
    return create_period(c, year, month, half_range(year, month, half), prev["id"] if prev else None)


# ---------- 管理端首页 ----------

def dashboard(c, date_str):
    y, m, d = (int(x) for x in date_str.split("-"))
    ponds = list_ponds(c, include_inactive=False)
    expected = [p for p in ponds if p["category"] != "暂无"]

    def day_values(yy, mm, dd):
        out = {}
        for r in c.execute(
            "SELECT shift, pond_id, kind, value FROM daily WHERE year=? AND month=? AND day=?", (yy, mm, dd)
        ):
            out.setdefault(r["shift"], {}).setdefault(r["pond_id"], {})[r["kind"]] = r["value"]
        return out

    today = day_values(y, m, d)
    shifts = {}
    for s in SHIFTS:
        vals = today.get(s, {})
        filled = [p for p in expected if p["id"] in vals]
        shifts[s] = {
            "filled": len(filled),
            "total": len(expected),
            "feed": sum(v.get("feed") or 0 for v in vals.values()),
            "injury": sum(v.get("injury") or 0 for v in vals.values()),
            "missing": [p["name"] for p in expected if p["id"] not in vals],
        }

    import datetime as _dt
    base = _dt.date(y, m, d)
    week = []
    for i in range(6, -1, -1):
        day = base - _dt.timedelta(days=i)
        vals = day_values(day.year, day.month, day.day)
        feed = sum(v.get("feed") or 0 for sv in vals.values() for v in sv.values())
        injury = sum(v.get("injury") or 0 for sv in vals.values() for v in sv.values())
        week.append({"date": day.isoformat(), "day": day.day, "feed": feed, "injury": injury})

    half = half_of_day(d)
    info = half_info(c, y, m, half)
    period = None
    if info["id"]:
        rep = period_report(c, info["id"])
        period = {"totals": rep["totals"], "day_range": rep["period"]["day_range"]}
    a, b = (1, 15) if half == "上" else (16, calendar.monthrange(y, m)[1])
    sums = c.execute(
        "SELECT kind, SUM(value) s FROM daily WHERE year=? AND month=? AND day BETWEEN ? AND ? GROUP BY kind",
        (y, m, a, b),
    ).fetchall()
    sums = {r["kind"]: r["s"] for r in sums}
    return {
        "date": date_str, "shifts": shifts, "week": week,
        "half": {**info, "feed_sum": sums.get("feed") or 0, "injury_sum": sums.get("injury") or 0},
        "period": period,
    }


def day_span(period):
    """'1-15' → (1, 15)；无法识别时按整月。"""
    last = calendar.monthrange(period["year"], period["month"])[1]
    try:
        a, b = str(period["day_range"]).replace("－", "-").split("-")
        a, b = int(a), int(b)
        if 1 <= a <= b <= 31:
            return a, min(b, last)
    except ValueError:
        pass
    return 1, last


def daily_sums(c, period):
    """这一期日期范围内，每个池号早晚记录的合计：{pond_id: {'feed': x, 'injury': y}}"""
    a, b = day_span(period)
    out = {}
    for r in c.execute(
        "SELECT pond_id, kind, SUM(value) AS s FROM daily"
        " WHERE year=? AND month=? AND day BETWEEN ? AND ? GROUP BY pond_id, kind",
        (period["year"], period["month"], a, b),
    ):
        out.setdefault(r["pond_id"], {})[r["kind"]] = r["s"]
    return out


INHERIT_FIELDS = ("conv_rate", "orig_weight", "orig_count")  # 留空时实时沿用上一期


def period_rows(c, pid, _memo=None):
    """这一期要显示的池号：启用中的池号 + 这一期已有记录的池号。
    伤苗没手填时，用每日早晚伤苗记录在这一期日期内的合计。
    转化率、原重量、原尾数没手填时，实时沿用上一期（上期转化率、上期现有重量、上期现尾数），
    上一期改了这里跟着变；手填的值优先。calc 里给出实际使用的值和 inherit（哪些是沿用的）。"""
    memo = {} if _memo is None else _memo
    if pid in memo:
        return memo[pid]
    period = get_period(c, pid)
    prev_rows = None  # 用到时才算上一期（上一期也可能沿用它的上一期）

    def prev_of(pond_id):
        nonlocal prev_rows
        if prev_rows is None:
            prev = previous_period(c, period["year"], period["month"], day_span(period)[0])
            prev_rows = {r["pond_id"]: r for r in period_rows(c, prev["id"], memo)} if prev else {}
        return prev_rows.get(pond_id)
    sums = daily_sums(c, period)
    day_from, day_to = day_span(period)
    latest_day = c.execute(
        "SELECT MAX(day) FROM daily WHERE year=? AND month=? AND day BETWEEN ? AND ? AND kind='feed'",
        (period["year"], period["month"], day_from, day_to),
    ).fetchone()[0]
    latest_feed = {}
    latest_shift = {}  # {pond_id: {"早": kg, "晚": kg}}：这一期最新投喂日每个池的早晚投喂量，用于提示漏喂/喂 0
    if latest_day is not None:
        latest_feed = {r["pond_id"]: dict(r) for r in c.execute(
            "SELECT pond_id,SUM(value) AS total,COUNT(*) AS shifts FROM daily WHERE year=? AND month=? AND day=? AND kind='feed' GROUP BY pond_id",
            (period["year"], period["month"], latest_day),
        )}
        for r in c.execute(
            "SELECT pond_id, shift, value FROM daily WHERE year=? AND month=? AND day=? AND kind='feed'",
            (period["year"], period["month"], latest_day),
        ):
            latest_shift.setdefault(r["pond_id"], {})[r["shift"]] = r["value"]
    rows = c.execute(
        """
        SELECT p.id AS pond_id, p.name, p.active, p.sort, p.category AS default_category, e.*
        FROM ponds p
        LEFT JOIN entries e ON e.pond_id = p.id AND e.period_id = ?
        WHERE p.active = 1 OR e.period_id IS NOT NULL
        ORDER BY p.sort, p.id
        """,
        (pid,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["pond_id"] = r[0]  # e.* 里也有 pond_id（没记录时为空），以第一列 p.id 为准
        d["category"] = d.get("category") or d["default_category"]
        s = sums.get(d["pond_id"], {})
        d["daily_feed_sum"] = s.get("feed")
        d["daily_injury_sum"] = s.get("injury")
        calc_input = dict(d)
        if d.get("feed_kg") is None and s.get("feed") is not None:
            calc_input["feed_kg"] = s["feed"]
        if d.get("injured") is None and s.get("injury") is not None:
            calc_input["injured"] = s["injury"]
        inherit = {}
        if period.get("carry_live") and any(d.get(f) is None for f in INHERIT_FIELDS):
            pr = prev_of(d["pond_id"])
            if pr:
                from_prev = {"conv_rate": pr["calc"]["conv_rate"], "orig_weight": pr["calc"]["cur_weight"],
                             "orig_count": pr["calc"]["cur_count"]}
                for f in INHERIT_FIELDS:
                    if d.get(f) is None and from_prev[f] is not None:
                        calc_input[f] = inherit[f] = from_prev[f]
        d["calc"] = compute_row(calc_input)
        for f in INHERIT_FIELDS:  # 实际使用的值（手填或沿用上一期）
            d["calc"][f] = calc_input.get(f)
        d["calc"]["inherit"] = inherit
        latest = latest_feed.get(d["pond_id"])
        d["calc"]["points_auto"] = d.get("daily_feed") is None and latest is not None
        d["calc"]["points_day"] = latest_day if d["calc"]["points_auto"] else None
        d["calc"]["points_feed_total"] = latest["total"] if d["calc"]["points_auto"] else None
        d["calc"]["points_shifts"] = latest["shifts"] if d["calc"]["points_auto"] else None
        if d["calc"]["points_auto"]:
            weight = d["calc"]["cur_weight"]
            d["calc"]["points"] = latest["total"] / weight if weight is not None and weight > 0 else None
        d["calc"]["feed_auto"] = d.get("feed_kg") is None and s.get("feed") is not None
        d["calc"]["feed_kg"] = calc_input.get("feed_kg")
        d["calc"]["injured"] = calc_input.get("injured")
        d["calc"]["injured_auto"] = d.get("injured") is None and s.get("injury") is not None
        shifts = latest_shift.get(d["pond_id"], {})
        d["calc"]["feed_day"] = latest_day
        d["calc"]["feed_shifts"] = {s_: shifts.get(s_) for s_ in SHIFTS}  # 没记录为 None
        out.append(d)
    memo[pid] = out
    return out


CN_TZ = timezone(timedelta(hours=8))  # 鳗场按北京时间；服务器本身可能是 UTC


def today_feed(c, year, month, now=None):
    """老板端漏喂提示用：今天（北京时间）每个池的早晚投喂量。
    看的不是当前月份时返回 None，前端改用那一期最后一次投喂日。"""
    now = now or datetime.now(CN_TZ)
    if (now.year, now.month) != (year, month):
        return None
    shifts = {}
    for r in c.execute(
        "SELECT pond_id, shift, value FROM daily WHERE year=? AND month=? AND day=? AND kind='feed'",
        (year, month, now.day),
    ):
        shifts.setdefault(str(r["pond_id"]), {})[r["shift"]] = r["value"]
    return {"date": now.strftime("%Y-%m-%d"), "day": now.day, "hour": now.hour, "minute": now.minute, "shifts": shifts}


def feed_alert(c, now=None):
    """管理端首页的漏喂提示：今天每池早晚投喂 + 各池现有重量。
    重量取今天所在半月的期报；这半月还没建期报（比如月初）就用上一期的。"""
    now = now or datetime.now(CN_TZ)
    half = "上" if now.day <= 15 else "下"
    p = find_half(c, now.year, now.month, half) or previous_period(c, now.year, now.month, 1 if half == "上" else 16)
    if p:
        rows = [{"pond_id": r["pond_id"], "name": r["name"], "category": r["category"],
                 "cur_weight": r["calc"].get("cur_weight")} for r in period_rows(c, p["id"])]
    else:  # 一期期报都没有：没法知道重量，所有在用的池都要喂
        rows = [{"pond_id": r["id"], "name": r["name"], "category": r["category"], "cur_weight": None}
                for r in c.execute("SELECT id, name, category FROM ponds WHERE active=1 ORDER BY sort, id")]
    for r in rows:
        w = r.pop("cur_weight")
        r["needs_feed"] = r["category"] != "暂无" and (w > 0 if w is not None else p is None)
    return {"today": today_feed(c, now.year, now.month, now), "rows": rows}


def period_report(c, pid):
    period = get_period(c, pid)
    rows = period_rows(c, pid)
    keys = ["pond_id", "name", "category", *INPUT_FIELDS, "note", "calc", "daily_feed_sum", "daily_injury_sum"]
    rows = [{k: r.get(k) for k in keys} for r in rows]
    a, b = day_span(period)
    feed_day = next((r["calc"].get("feed_day") for r in rows if r["calc"].get("feed_day")), None)
    return {"period": {**period, "day_from": a, "day_to": b, "feed_day": feed_day}, "rows": rows, "totals": compute_totals(rows)}


def save_entries(c, pid, rows: list[dict], partial=False):
    get_period(c, pid)
    for r in rows:
        if partial:
            selected=r.get('fields')
            allowed={*INPUT_FIELDS,'category','note'}
            if not isinstance(selected,list) or not selected or len(selected)!=len(set(selected)) or any(f not in allowed for f in selected):
                raise ValueError('修改字段不正确，请刷新页面重试')
            values=[]
            for f in selected:
                if f in INPUT_FIELDS:
                    values.append(num(r.get(f)))
                elif f=='category':
                    if r.get(f) not in CATEGORIES:
                        raise ValueError('分类不正确')
                    values.append(r[f])
                else:
                    values.append(str(r.get(f) or '').strip() or None)
            c.execute(f"INSERT INTO entries(period_id,pond_id,{','.join(selected)}) VALUES(?, ?,{','.join('?' for _ in selected)}) ON CONFLICT(period_id,pond_id) DO UPDATE SET {','.join(f+'=excluded.'+f for f in selected)}",(pid,int(r['pond_id']),*values))
            continue
        vals = {f: num(r.get(f)) for f in INPUT_FIELDS}
        cat = r.get("category") if r.get("category") in CATEGORIES else None
        note = (r.get("note") or "").strip() or None
        c.execute(
            f"""
            INSERT INTO entries(period_id, pond_id, category, {", ".join(INPUT_FIELDS)}, note)
            VALUES(?, ?, ?, {", ".join("?" * len(INPUT_FIELDS))}, ?)
            ON CONFLICT(period_id, pond_id) DO UPDATE SET
              category=excluded.category,
              {", ".join(f"{f}=excluded.{f}" for f in INPUT_FIELDS)},
              note=excluded.note
            """,
            (pid, int(r["pond_id"]), cat, *vals.values(), note),
        )


# ---------- 每日投食 / 伤苗 ----------

def daily_grid(c, year, month, kind):
    if kind not in DAILY_KINDS:
        raise ValueError("类型不正确")
    ponds = [
        dict(r)
        for r in c.execute(
            """
            SELECT id, name, category FROM ponds p
            WHERE active = 1 OR EXISTS(
              SELECT 1 FROM daily d WHERE d.pond_id=p.id AND d.year=? AND d.month=? AND d.kind=?)
            ORDER BY sort, id
            """,
            (year, month, kind),
        )
    ]
    values = {}
    for r in c.execute(
        "SELECT day, shift, pond_id, value FROM daily WHERE year=? AND month=? AND kind=?",
        (year, month, kind),
    ):
        values[f"{r['day']}|{r['shift']}|{r['pond_id']}"] = r["value"]
    return {
        "year": year,
        "month": month,
        "kind": kind,
        "days": calendar.monthrange(year, month)[1],
        "ponds": ponds,
        "values": values,
    }


def save_daily(c, year, month, kind, cells: list[dict]):
    if kind not in DAILY_KINDS:
        raise ValueError("类型不正确")
    for cell in cells:
        day, shift, pond_id = int(cell["day"]), cell["shift"], int(cell["pond_id"])
        if shift not in SHIFTS:
            raise ValueError("班次不正确")
        v = num(cell.get("value"))
        if v is None:
            c.execute(
                "DELETE FROM daily WHERE year=? AND month=? AND day=? AND shift=? AND pond_id=? AND kind=?",
                (year, month, day, shift, pond_id, kind),
            )
        else:
            c.execute(
                "INSERT INTO daily VALUES(?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET value=excluded.value",
                (year, month, day, shift, pond_id, kind, v),
            )
