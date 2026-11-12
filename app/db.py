import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "demo.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS deleted_periods(id INTEGER PRIMARY KEY,payload TEXT NOT NULL,deleted_at TEXT DEFAULT CURRENT_TIMESTAMP,restored_at TEXT);
CREATE TABLE IF NOT EXISTS wage_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wage_legacy_links(payroll_id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wage_legacy_promotions(payroll_id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS wage_employees(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  start_month TEXT NOT NULL, end_month TEXT, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS wage_rates(
  employee_id INTEGER NOT NULL REFERENCES wage_employees(id),
  effective_month TEXT NOT NULL, salary_cents INTEGER NOT NULL,
  PRIMARY KEY(employee_id,effective_month)
);
CREATE TABLE IF NOT EXISTS wage_events(
  id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES wage_employees(id),
  date TEXT NOT NULL, kind TEXT NOT NULL, amount_cents INTEGER NOT NULL DEFAULT 0,
  leave_days TEXT NOT NULL DEFAULT '0', note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS wage_settlements(
  id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES wage_employees(id),
  year INTEGER NOT NULL, month INTEGER NOT NULL, detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS wage_overrides(
  employee_id INTEGER NOT NULL REFERENCES wage_employees(id),
  year INTEGER NOT NULL, month INTEGER NOT NULL,
  salary_cents INTEGER, deduction_cents INTEGER, note TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(employee_id, year, month)
);
CREATE INDEX IF NOT EXISTS wage_events_month ON wage_events(substr(date,1,7));
CREATE UNIQUE INDEX IF NOT EXISTS wage_settled_month ON wage_settlements(employee_id,year,month) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS payroll(
  id INTEGER PRIMARY KEY,
  year INTEGER NOT NULL, month INTEGER NOT NULL, name TEXT NOT NULL,
  salary_cents INTEGER NOT NULL, deduction_cents INTEGER NOT NULL DEFAULT 0,
  bonus_cents INTEGER NOT NULL DEFAULT 0, leave_days TEXT NOT NULL DEFAULT '0',
  paid INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
  UNIQUE(year, month, name)
);
CREATE TABLE IF NOT EXISTS ponds(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL DEFAULT '新',
  active INTEGER NOT NULL DEFAULT 1,
  sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS periods(
  id INTEGER PRIMARY KEY,
  year INTEGER NOT NULL,
  month INTEGER NOT NULL,
  day_range TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  carry_live INTEGER NOT NULL DEFAULT 0,  -- 1：转化率、原重量、原尾数空着时实时沿用上一期
  UNIQUE(year, month, day_range)
);
CREATE TABLE IF NOT EXISTS entries(
  period_id INTEGER NOT NULL REFERENCES periods(id) ON DELETE CASCADE,
  pond_id INTEGER NOT NULL REFERENCES ponds(id) ON DELETE CASCADE,
  category TEXT,
  feed_kg REAL, conv_rate REAL, orig_weight REAL, injured REAL,
  orig_count REAL, daily_feed REAL, cur_count_override REAL, note TEXT,
  PRIMARY KEY(period_id, pond_id)
);
CREATE TABLE IF NOT EXISTS daily(
  year INTEGER NOT NULL, month INTEGER NOT NULL, day INTEGER NOT NULL,
  shift TEXT NOT NULL, pond_id INTEGER NOT NULL REFERENCES ponds(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, value REAL NOT NULL,
  PRIMARY KEY(year, month, day, shift, pond_id, kind)
);
"""


def db_path(farm=None):
    """某个厂的数据库文件；不传就用当前请求所属的厂"""
    from . import farms
    return DATA_DIR / farms.get(farm)["db"]


def connect(farm=None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path(farm))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def tx(farm=None):
    conn = connect(farm)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(farm=None):
    """建表（已有的表不动）。新厂不再自动生成池号：管理端第一次进入时自己填池数。"""
    with tx(farm) as c:
        c.executescript(SCHEMA)
        if "carry_live" not in [r[1] for r in c.execute("PRAGMA table_info(periods)")]:  # 老数据库补这一列
            c.execute("ALTER TABLE periods ADD COLUMN carry_live INTEGER NOT NULL DEFAULT 0")
        from .payroll_smart import seed_legacy
        seed_legacy(c)
        # 2026-11-01：期初数据改为实时沿用上一期；以前复制成固定值、且和上一期一致的，改回自动（只做一次）
        if not c.execute("SELECT 1 FROM wage_metadata WHERE key='carry_live_v1'").fetchone():
            from .service import release_carried
            n = release_carried(c)
            c.execute("INSERT INTO wage_metadata(key, value) VALUES('carry_live_v1', ?)", (str(n),))


def init_all():
    from . import farms
    for f in farms.enabled():
        init_db(f["id"])
