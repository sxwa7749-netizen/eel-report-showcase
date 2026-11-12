import sqlite3
import unittest

from app.db import SCHEMA
from app import payroll_smart as wage


class SmartPayrollTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA foreign_keys=ON')
        self.c.executescript(SCHEMA)
        self.eid = wage.save_employee(self.c, dict(name='员工甲', salary='3100', effective_month='2026-10'))['id']

    def tearDown(self):
        self.c.close()

    def event(self, date, kind='leave', **kw):
        return wage.save_event(self.c, dict(employee_id=self.eid, date=date, kind=kind, leave_days='1', **kw))

    def row(self, year=2026, month=10):
        return wage.overview(self.c, year, month)['rows'][0]

    def test_monthly_auto_draft_and_actual_days(self):
        self.assertEqual(self.row()['net_cents'], 310000)
        self.event('2026-10-01', end_date='2026-10-03')
        self.event('2026-10-04', 'bonus', amount='200.25')
        self.event('2026-10-05', 'advance', amount='500.10')
        row = self.row()
        self.assertEqual(row['deduction_cents'], 30000)
        self.assertEqual(row['net_cents'], 250015)
        self.assertEqual(self.row(2026, 11)['net_cents'], 310000)
        self.assertEqual(self.row(2026, 11)['events'], [])

    def test_leap_february_and_aggregate_rounding(self):
        eid = wage.save_employee(self.c, dict(name='员工乙', salary='3000', effective_month='2024-02'))['id']
        for day in (1, 2, 3):
            wage.save_event(self.c, dict(employee_id=eid, date=f'2024-02-{day:02}', kind='leave', leave_days='0.5'))
        row = wage.overview(self.c, 2024, 2)['rows'][0]
        self.assertEqual(row['month_days'], 29)
        self.assertEqual(row['deduction_cents'], 15517)

    def test_cross_month_leave(self):
        self.event('2026-10-31', end_date='2026-11-01')
        self.assertEqual(self.row()['deduction_cents'], 10000)
        self.assertEqual(self.row(2026, 11)['deduction_cents'], 10333)

    def test_duplicate_leave_and_transaction_rollback(self):
        self.event('2026-10-02')
        self.c.commit()
        with self.assertRaises(ValueError), self.c:
            self.event('2026-10-01', end_date='2026-10-03')
        self.assertEqual(len(wage.events(self.c, 2026, 10)), 1)

    def test_settlement_lock_reopen_and_audit_snapshot(self):
        event_id = self.event('2026-10-01')['id']
        b = dict(employee_id=self.eid, year=2026, month=10, expected_net_cents=300000)
        wage.settle(self.c, b)
        wage.settle(self.c, b)
        self.assertEqual(self.c.execute('SELECT count(*) FROM wage_settlements').fetchone()[0], 1)
        with self.assertRaises(ValueError):
            wage.delete_event(self.c, event_id)
        with self.assertRaises(ValueError):
            self.event('2026-10-02')
        with self.assertRaises(ValueError):
            wage.save_employee(self.c, dict(id=self.eid, name='员工甲', salary='4000', effective_month='2026-10'))
        wage.save_employee(self.c, dict(id=self.eid, name='员工甲', salary='4000', effective_month='2026-11'))
        self.assertEqual(self.row()['net_cents'], 300000)
        self.assertEqual(self.row(2026, 11)['net_cents'], 400000)
        wage.reopen(self.c, b)
        wage.delete_event(self.c, event_id)
        self.assertEqual(self.row()['net_cents'], 310000)
        self.assertIsNotNone(self.c.execute('SELECT revoked_at FROM wage_settlements').fetchone()[0])

    def test_stale_preview_and_negative_net(self):
        with self.assertRaises(ValueError):
            wage.settle(self.c, dict(employee_id=self.eid, year=2026, month=10, expected_net_cents=1))
        self.event('2026-10-01', 'advance', amount='4000')
        self.assertLess(self.row()['net_cents'], 0)
        with self.assertRaises(ValueError):
            wage.settle(self.c, dict(employee_id=self.eid, year=2026, month=10))

    def test_legacy_preserved_and_not_double_counted_after_rename(self):
        self.c.execute("INSERT INTO payroll(year,month,name,salary_cents,deduction_cents,bonus_cents,paid) VALUES(2026,10,'旧员工',300000,12345,100,1)")
        wage.seed_legacy(self.c)
        result = wage.overview(self.c, 2026, 10)
        self.assertEqual(result['total_cents'], 310000+287755)
        self.assertEqual(result['paid_cents'], 287755)
        old = next(e for e in result['employees'] if e['name'] == '旧员工')
        wage.save_employee(self.c, dict(id=old['id'], name='旧员工改名', salary='3000', effective_month='2026-11'))
        wage.seed_legacy(self.c)
        result = wage.overview(self.c, 2026, 10)
        self.assertEqual(result['total_cents'], 310000+287755)
        self.assertEqual(len(result['employees']), 2)
        with self.assertRaises(ValueError):
            wage.save_event(self.c, dict(employee_id=old['id'], date='2026-10-01', kind='leave', leave_days='1'))

    def test_stop_and_resume_and_invalid_input(self):
        wage.save_employee(self.c, dict(id=self.eid, name='员工甲', salary='3100', effective_month='2026-10', end_month='2026-10'))
        self.assertEqual(wage.overview(self.c, 2026, 11)['rows'], [])
        with self.assertRaises(ValueError):
            self.event('2026-11-01')
        for days in ('NaN', '0', '1.1', '0.25'):
            with self.assertRaises(ValueError):
                wage.save_event(self.c, dict(employee_id=self.eid, date='2026-10-01', kind='leave', leave_days=days))

    def test_optional_legacy_upgrade_keeps_original_and_does_not_double_count(self):
        rid=self.c.execute("INSERT INTO payroll(year,month,name,salary_cents) VALUES(2026,10,'员工甲',310000)").lastrowid
        wage.seed_legacy(self.c)
        before=wage.overview(self.c,2026,10)
        self.assertEqual(before['total_cents'],310000)
        self.assertEqual(before['rows'],[])
        self.assertTrue(before['legacy']['rows'][0]['can_upgrade'])
        wage.upgrade_legacy(self.c,rid)
        wage.upgrade_legacy(self.c,rid)
        after=wage.overview(self.c,2026,10)
        self.assertEqual(after['total_cents'],310000)
        self.assertEqual(after['legacy']['rows'],[])
        self.assertEqual(self.c.execute('SELECT salary_cents FROM payroll WHERE id=?',(rid,)).fetchone()[0],310000)
        self.event('2026-10-01')
        self.assertEqual(self.row()['net_cents'],300000)
        from app import payroll
        with self.assertRaises(ValueError):
            payroll.save(self.c,dict(id=rid,year=2026,month=10,name='员工甲',salary='100'))


    def override(self, **kw):
        return wage.save_override(self.c, dict(employee_id=self.eid, year=2026, month=10, **kw))

    def test_manual_override_salary_and_deduction(self):
        self.event('2026-10-01')
        self.override(salary='1550', note='10月16日入职，半月工资')
        row = self.row()
        self.assertTrue(row['salary_manual'])
        self.assertEqual(row['salary_auto_cents'], 310000)
        self.assertEqual(row['salary_cents'], 155000)
        self.assertEqual(row['deduction_cents'], 5000)  # 按手动月薪折算
        self.override(salary='1550', deduction='0', note='请假已调休，不扣')
        row = self.row()
        self.assertTrue(row['deduction_manual'])
        self.assertEqual(row['deduction_auto_cents'], 5000)
        self.assertEqual(row['net_cents'], 155000)
        # 下个月不受影响
        self.assertEqual(self.row(2026, 11)['net_cents'], 310000)
        # 两项都留空即恢复自动
        self.override(salary='', deduction='')
        row = self.row()
        self.assertFalse(row['salary_manual'] or row['deduction_manual'])
        self.assertEqual(row['net_cents'], 300000)

    def test_override_requires_reason_and_respects_lock(self):
        with self.assertRaises(ValueError):
            self.override(salary='1000')
        wage.settle(self.c, dict(employee_id=self.eid, year=2026, month=10))
        with self.assertRaises(ValueError):
            self.override(salary='1000', note='改数')
        wage.reopen(self.c, dict(employee_id=self.eid, year=2026, month=10))
        self.override(salary='1000', note='改数')
        self.assertEqual(self.row()['net_cents'], 100000)

    def test_adjust_plus_and_minus_in_snapshot(self):
        self.event('2026-10-03', 'adjust', amount='100', note='全勤奖')
        self.event('2026-10-04', 'adjust', amount='30.5', sign='-', note='损坏工具')
        row = self.row()
        self.assertEqual(row['adjust_cents'], 6950)
        self.assertEqual(row['net_cents'], 316950)
        with self.assertRaises(ValueError):
            self.event('2026-10-05', 'adjust', amount='10')  # 没写原因
        wage.settle(self.c, dict(employee_id=self.eid, year=2026, month=10, expected_net_cents=316950))
        self.assertEqual(self.row()['adjust_cents'], 6950)
        self.assertTrue(self.row()['paid'])


    def test_delete_employee_only_without_history(self):
        # 刚添加、没有任何记录：可以删
        eid = wage.save_employee(self.c, dict(name='加错的人', salary='3000', effective_month='2026-10'))['id']
        self.assertTrue(next(e for e in wage.employees(self.c, 2026, 10) if e['id'] == eid)['deletable'])
        wage.delete_employee(self.c, eid)
        self.assertFalse(any(e['id'] == eid for e in wage.employees(self.c, 2026, 10)))
        self.assertEqual(self.c.execute('SELECT count(*) FROM wage_rates WHERE employee_id=?', (eid,)).fetchone()[0], 0)
        # 有记录：不能删，只能设为离职
        self.event('2026-10-01')
        self.assertFalse(wage.employees(self.c, 2026, 10)[0]['deletable'])
        with self.assertRaises(ValueError):
            wage.delete_employee(self.c, self.eid)
        wage.save_employee(self.c, dict(id=self.eid, name='员工甲', salary='3100', effective_month='2026-10', end_month='2026-10'))
        self.assertEqual(len(wage.overview(self.c, 2026, 10)['rows']), 1)   # 本月照常
        self.assertEqual(wage.overview(self.c, 2026, 11)['rows'], [])       # 下月起不再生成
        self.assertTrue(wage.employees(self.c, 2026, 11)[0]['left'])
        # 只手动改过数的员工也算有记录
        eid2 = wage.save_employee(self.c, dict(name='员工丙', salary='3000', effective_month='2026-10'))['id']
        wage.save_override(self.c, dict(employee_id=eid2, year=2026, month=10, salary='1000', note='试用'))
        with self.assertRaises(ValueError):
            wage.delete_employee(self.c, eid2)


    def test_settle_all_and_stale_amount(self):
        eid2 = wage.save_employee(self.c, dict(name='员工乙', salary='2000', effective_month='2026-10'))['id']
        rows = {r['employee_id']: r['net_cents'] for r in wage.overview(self.c, 2026, 10)['rows']}
        self.c.commit()
        # 金额对不上：整批不发（事务回滚，第一个人也不会被发放）
        with self.assertRaises(ValueError), self.c:
            wage.settle_all(self.c, dict(year=2026, month=10, expected={self.eid: rows[self.eid], eid2: 1}))
        self.assertEqual(wage.overview(self.c, 2026, 10)['paid_cents'], 0)
        r = wage.settle_all(self.c, dict(year=2026, month=10, expected={str(k): v for k, v in rows.items()}))
        self.assertEqual(r['count'], 2)
        self.assertEqual(r['total_cents'], 510000)
        self.assertTrue(all(x['paid'] for x in wage.overview(self.c, 2026, 10)['rows']))
        # 已发放的再发一次不会重复
        self.assertEqual(wage.settle_all(self.c, dict(year=2026, month=10, expected=rows))['count'], 0)


if __name__ == '__main__':
    unittest.main()
