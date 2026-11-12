import sqlite3
import unittest
from datetime import datetime

from app.db import SCHEMA
from app import service


class TodayFeedTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        self.c.execute("INSERT INTO ponds(id, name) VALUES (1, 'A1'), (2, 'A2')")
        rows = [
            (2026, 11, 1, '早', 1, 'feed', 12.5),
            (2026, 11, 1, '早', 2, 'feed', 0),
            (2026, 11, 1, '早', 1, 'injury', 3),   # 伤苗不算投喂
            (2026, 10, 31, '晚', 1, 'feed', 9),    # 昨天的不算
        ]
        self.c.executemany("INSERT INTO daily VALUES (?,?,?,?,?,?,?)", rows)

    def tearDown(self):
        self.c.close()

    def test_returns_today_shifts_in_beijing_time(self):
        now = datetime(2026, 11, 1, 9, 30, tzinfo=service.CN_TZ)
        t = service.today_feed(self.c, 2026, 11, now)
        self.assertEqual(t['date'], '2026-11-01')
        self.assertEqual((t['day'], t['hour'], t['minute']), (1, 9, 30))
        self.assertEqual(t['shifts'], {'1': {'早': 12.5}, '2': {'早': 0}})

    def test_other_month_returns_none(self):
        now = datetime(2026, 11, 1, 9, 30, tzinfo=service.CN_TZ)
        self.assertIsNone(service.today_feed(self.c, 2026, 10, now))

    def test_feed_alert_without_period_needs_all_active_ponds(self):
        now = datetime(2026, 11, 1, 9, 30, tzinfo=service.CN_TZ)
        a = service.feed_alert(self.c, now)
        self.assertEqual(a['today']['shifts']['1'], {'早': 12.5})
        self.assertEqual([r['needs_feed'] for r in a['rows']], [True, True])

    def test_feed_alert_uses_previous_period_weight_at_month_start(self):
        pid = service.create_period(self.c, 2026, 10, '16-31', None)
        service.save_entries(self.c, pid, [
            {'pond_id': 1, 'orig_weight': '500'},   # 有鱼
            {'pond_id': 2, 'orig_weight': '0'},     # 空池
        ])
        a = service.feed_alert(self.c, datetime(2026, 11, 1, 9, 30, tzinfo=service.CN_TZ))
        self.assertEqual({r['name']: r['needs_feed'] for r in a['rows']}, {'A1': True, 'A2': False})

    def test_default_now_uses_utc_plus_8(self):
        self.assertEqual(service.CN_TZ.utcoffset(None).total_seconds(), 8 * 3600)


if __name__ == '__main__':
    unittest.main()
