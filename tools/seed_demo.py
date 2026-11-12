"""Generate fictional sample data for local demos and screenshots.

All numbers are random and do not come from any real farm.
Usage:  python -m tools.seed_demo   (writes to DATA_DIR, default ./data)
"""
import random
from datetime import datetime

from app import service
from app.db import init_db, tx

random.seed(7)


def main():
    init_db("demo")
    now = datetime.now(service.CN_TZ)
    with tx("demo") as c:
        if c.execute("SELECT COUNT(*) FROM ponds").fetchone()[0]:
            print("demo data already exists, skipped")
            return
        service.setup_ponds(c, 12, "老")
        ponds = c.execute("SELECT id FROM ponds ORDER BY sort").fetchall()
        for i, p in enumerate(ponds):
            if i >= 8:
                c.execute("UPDATE ponds SET category='新' WHERE id=?", (p["id"],))

        y, m = now.year, now.month
        prev_y, prev_m = (y, m - 1) if m > 1 else (y - 1, 12)
        first = service.create_period(c, prev_y, prev_m, "16-31", None)
        rows = []
        for i, p in enumerate(ponds):
            new = i >= 8
            rows.append({
                "pond_id": p["id"],
                "category": "新" if new else "老",
                "orig_weight": str(random.randint(300, 600) if new else random.randint(1200, 2600)),
                "orig_count": str(random.randint(3000, 6000) if new else random.randint(4000, 9000)),
                "conv_rate": str(round(random.uniform(0.55, 0.75), 2)),
                "feed_kg": str(random.randint(80, 220)),
                "injured": str(random.randint(0, 40)),
            })
        service.save_entries(c, first, rows)

        service.ensure_half(c, y, m, "上" if now.day <= 15 else "下")
        start = 1 if now.day <= 15 else 16
        daily = []
        for day in range(start, now.day + 1):
            for shift in ("早", "晚"):
                if day == now.day and shift == "晚":
                    continue
                for p in ponds:
                    daily.append((y, m, day, shift, p["id"], "feed", round(random.uniform(4, 14), 1)))
                    if random.random() < 0.15:
                        daily.append((y, m, day, shift, p["id"], "injury", random.randint(1, 6)))
        c.executemany("INSERT OR REPLACE INTO daily VALUES (?,?,?,?,?,?,?)", daily)
    print("demo data created")


if __name__ == "__main__":
    main()
