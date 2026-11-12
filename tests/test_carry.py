"""期初数据实时沿用上一期 + 新一期自动开始"""
import sqlite3
import unittest
from datetime import datetime

from app.db import SCHEMA
from app import service

CN = service.CN_TZ


class CarryTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        self.c.executemany("INSERT INTO ponds(id, name, category, sort) VALUES (?,?,?,?)", [(1, '1#', '老', 1), (2, '2#', '新', 2)])
        self.oct1 = service.create_period(self.c, 2026, 10, '1-15', None)
        service.save_entries(self.c, self.oct1, [
            {'pond_id': 1, 'category': '老', 'orig_weight': '1000', 'orig_count': '2000', 'conv_rate': '0.8', 'feed_kg': '100', 'injured': '10'},
            {'pond_id': 2, 'category': '新', 'orig_weight': '500', 'orig_count': '1000', 'conv_rate': '0.5'},
        ])

    def tearDown(self):
        self.c.close()

    def row(self, pid, pond=1):
        return next(r for r in service.period_rows(self.c, pid) if r['pond_id'] == pond)

    def test_new_period_follows_previous_live(self):
        oct2 = service.ensure_half(self.c, 2026, 10, '下')
        r = self.row(oct2)
        self.assertIsNone(r['orig_weight'])                 # 没有存固定值
        self.assertAlmostEqual(r['calc']['orig_weight'], 1080)  # 1000 + 100×0.8
        self.assertEqual(r['calc']['orig_count'], 1990)
        self.assertEqual(r['calc']['conv_rate'], 0.8)
        self.assertEqual(set(r['calc']['inherit']), {'conv_rate', 'orig_weight', 'orig_count'})
        self.assertEqual(r['category'], '老')
        # 上一期改了，这一期跟着变
        service.save_entries(self.c, self.oct1, [{'pond_id': 1, 'category': '老', 'orig_weight': '1000', 'orig_count': '2000', 'conv_rate': '0.8', 'feed_kg': '200', 'injured': '10'}])
        self.assertAlmostEqual(self.row(oct2)['calc']['orig_weight'], 1160)
        # 手填优先
        service.save_entries(self.c, oct2, [{'pond_id': 1, 'category': '老', 'orig_weight': '999'}])
        r = self.row(oct2)
        self.assertEqual(r['calc']['orig_weight'], 999)
        self.assertNotIn('orig_weight', r['calc']['inherit'])
        self.assertIn('orig_count', r['calc']['inherit'])

    def test_chain_of_three_periods(self):
        oct2 = service.ensure_half(self.c, 2026, 10, '下')
        service.save_entries(self.c, oct2, [{'pond_id': 1, 'category': '老', 'feed_kg': '50'}])  # 只填投料
        nov1 = service.ensure_half(self.c, 2026, 11, '上')
        self.assertAlmostEqual(self.row(nov1)['calc']['orig_weight'], 1080 + 50 * 0.8)
        report = service.period_report(self.c, nov1)
        self.assertAlmostEqual(report['totals']['总计']['orig_weight'], 1120 + 500)

    def test_release_carried_converts_old_snapshots(self):
        # 模拟以前的做法：新一期把上一期的数复制成固定值；第 2 口池手填了不同的原重量
        oct2 = service.create_period(self.c, 2026, 10, '16-31', None)
        self.c.execute("UPDATE periods SET carry_live=0 WHERE id=?", (oct2,))  # 以前建的期报没有这个标记
        service.save_entries(self.c, oct2, [
            {'pond_id': 1, 'category': '老', 'orig_weight': '1080', 'orig_count': '1990', 'conv_rate': '0.8'},
            {'pond_id': 2, 'category': '新', 'orig_weight': '500', 'orig_count': '1000', 'conv_rate': '0.6'},
        ])
        changed = service.release_carried(self.c)
        self.assertEqual(changed, 5)  # 1#：三项；2#：原重量、原尾数（转化率 0.6≠0.5 是手填的，保留）
        r1, r2 = self.row(oct2, 1), self.row(oct2, 2)
        self.assertIsNone(r1['orig_weight'])
        self.assertAlmostEqual(r1['calc']['orig_weight'], 1080)
        self.assertEqual(r2['conv_rate'], 0.6)
        self.assertEqual(r2['calc']['orig_weight'], 500)
        self.assertEqual(service.release_carried(self.c), 0)  # 再跑一次不变

    def test_old_imported_period_is_left_alone(self):
        # 导入的期报（carry_live=0）和上一期对不上：不转换，空着的格子也不去沿用上一期
        oct2 = service.create_period(self.c, 2026, 10, '16-31', None)
        self.c.execute("UPDATE periods SET carry_live=0 WHERE id=?", (oct2,))
        service.save_entries(self.c, oct2, [
            {'pond_id': 1, 'category': '老', 'orig_weight': '5000', 'orig_count': '9000', 'conv_rate': '0.7'},
            {'pond_id': 2, 'category': '新'},  # 空池，什么都没填
        ])
        self.assertEqual(service.release_carried(self.c), 0)
        r2 = self.row(oct2, 2)
        self.assertIsNone(r2['calc']['orig_weight'])
        self.assertEqual(r2['calc']['inherit'], {})
        self.assertEqual(self.row(oct2, 1)['calc']['orig_weight'], 5000)

    def test_ensure_current_half(self):
        p = service.ensure_current_half(self.c, datetime(2026, 11, 1, 0, 5, tzinfo=CN))
        self.assertEqual(service.get_period(self.c, p)['day_range'], '1-15')
        self.assertEqual(service.ensure_current_half(self.c, datetime(2026, 11, 3, tzinfo=CN)), p)  # 不重复建
        # 中间漏掉的 10 月下半月不会凭空建出来；11 月上半月沿用 10 月上半月
        self.assertAlmostEqual(self.row(p)['calc']['orig_weight'], 1080)

    def test_new_farm_without_periods_is_not_auto_started(self):
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        c.execute("INSERT INTO ponds(name, category, sort) VALUES ('1#', '新', 1)")
        self.assertIsNone(service.ensure_current_half(c, datetime(2026, 11, 1, tzinfo=CN)))
        c.close()


if __name__ == '__main__':
    unittest.main()
