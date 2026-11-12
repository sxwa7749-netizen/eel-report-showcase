"""报表计算逻辑，对应原 Excel「数据录入」「报表」「报表竖版」里的公式。"""
from decimal import Decimal, ROUND_DOWN

CATEGORIES = ["老", "新", "暂无"]
INPUT_FIELDS = [
    "feed_kg",           # 投料 kg
    "conv_rate",         # 转化率（小数，0.85 = 85%）
    "orig_weight",       # 原重量 kg
    "injured",           # 伤苗 尾
    "orig_count",        # 原尾数
    "daily_feed",        # 当日投喂 kg
    "cur_count_override",  # 现尾数（手填时覆盖自动计算）
]


def trunc1(x):
    """Excel 的 TRUNC(x, 1)：保留 1 位小数，直接截断不四舍五入。"""
    if x is None:
        return None
    return float(Decimal(repr(x)).quantize(Decimal("0.1"), rounding=ROUND_DOWN))


def compute_row(e: dict) -> dict:
    feed, conv, orig = e.get("feed_kg"), e.get("conv_rate"), e.get("orig_weight")
    injured, orig_count = e.get("injured"), e.get("orig_count")
    daily_feed, override = e.get("daily_feed"), e.get("cur_count_override")

    # 增重 = 投料 × 转化率
    gain = feed * conv if feed is not None and conv is not None else None
    # 现有重量 = 原重量 + 增重
    cur_weight = orig + (gain or 0) if orig is not None else None
    # 现尾数 = 原尾数 − 伤苗（手填优先）
    if override is not None:
        cur_count = override
    elif orig_count is not None:
        cur_count = orig_count - (injured or 0)
    else:
        cur_count = None
    # Excel 里点数、P数的分母都是 原重量 + 投料×转化率（空格按 0 算）
    base = (orig or 0) + (feed or 0) * (conv or 0)
    points = daily_feed * 2 / base if daily_feed is not None and base > 0 else None
    p = trunc1(cur_count / base) if cur_count is not None and base > 0 else None

    return {
        "gain": gain,
        "cur_weight": cur_weight,
        "cur_count": cur_count,
        "points": points,
        "p": p,
        "count_is_manual": override is not None,
    }


def _sum(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else 0


def compute_totals(rows: list[dict]) -> dict:
    """rows: 每项含 category / 输入字段 / calc。返回 总计、老、新、暂无 四组合计。"""
    groups = {"总计": rows}
    for c in CATEGORIES:
        groups[c] = [r for r in rows if r.get("category") == c]
    out = {}
    for name, rs in groups.items():
        weight = _sum(r["calc"]["cur_weight"] for r in rs)
        count = _sum(r["calc"]["cur_count"] for r in rs)
        point_rows = [r for r in rs if r["calc"].get("points") is not None
                      and r["calc"].get("cur_weight") is not None and r["calc"]["cur_weight"] > 0]
        point_weight = sum(r["calc"]["cur_weight"] for r in point_rows)
        point_days = {r["calc"].get("points_day") for r in point_rows}
        out[name] = {
            "ponds": len(rs),
            "feed_kg": _sum(r["calc"].get("feed_kg", r.get("feed_kg")) for r in rs),
            "gain": _sum(r["calc"]["gain"] for r in rs),
            "orig_weight": _sum(r["calc"].get("orig_weight", r.get("orig_weight")) for r in rs),
            "cur_weight": weight,
            "injured": _sum(r["calc"].get("injured", r.get("injured")) for r in rs),
            "cur_count": count,
            "daily_feed": _sum(r.get("daily_feed") for r in rs),
            "avg_p": trunc1(count / weight) if weight else None,
            "points": sum(r["calc"]["points"] * r["calc"]["cur_weight"] for r in point_rows) / point_weight if point_weight else None,
            "points_ponds": len(point_rows),
            "points_expected": sum(1 for r in rs if (r["calc"].get("cur_weight") or 0) > 0),
            "points_day": next(iter(point_days)) if len(point_days) == 1 else None,
        }
    return out
