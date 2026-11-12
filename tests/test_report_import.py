from io import BytesIO
import sqlite3
import unittest

from openpyxl import load_workbook

from app import excel_export, farms, report_import, service
from app.db import SCHEMA


def database():
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.executescript(SCHEMA)
    return c


class ReportImportTests(unittest.TestCase):
    def setUp(self):
        self.token=farms.current.set('demo2')
        self.source=database();self.target=database()
        service.setup_ponds(self.source,42)
        previous=service.create_period(self.source,2026,10,'16-31')
        service.save_entries(self.source,previous,[dict(pond_id=i,category='新' if i<20 else '老',orig_weight=1000+i,orig_count=10000+i,conv_rate=.85) for i in range(1,43)])
        self.pid=service.ensure_half(self.source,2026,11,'上')
        service.save_entries(self.source,self.pid,[dict(pond_id=1,category='新',cur_count_override=7777,note='保留原值'),dict(pond_id=2,category='新',feed_kg=0,daily_feed=0)])
        for kind in ('feed','injury'):
            service.save_daily(self.source,2026,11,kind,[dict(day=1,shift=s,pond_id=i,value=0 if i==2 else 1) for i in range(1,43) for s in ('早','晚')])

    def tearDown(self):
        self.source.close();self.target.close();farms.current.reset(self.token)

    def export(self):
        return excel_export.export_period(self.source,self.pid)[0]

    def snapshot(self,c,pid):
        return {r['name']:{k:r['calc'].get(k) for k in ('cur_weight','cur_count','p','points','feed_kg','conv_rate','orig_weight')} for r in service.period_report(c,pid)['rows']}

    def test_full_roundtrip_42_ponds_live_inheritance_and_zero(self):
        raw=self.export();plan=report_import.parse(raw)
        self.assertEqual(report_import.preview(self.target,plan)['ponds'],42)
        self.assertEqual(self.target.execute('SELECT COUNT(*) FROM periods').fetchone()[0],0)
        restored=report_import.apply(self.target,plan)['period_id']
        self.assertEqual(self.snapshot(self.source,self.pid),self.snapshot(self.target,restored))
        self.assertEqual(self.source.execute('SELECT COUNT(*) FROM daily').fetchone()[0],self.target.execute('SELECT COUNT(*) FROM daily').fetchone()[0])
        report_import.apply(self.target,plan)
        self.assertEqual(self.target.execute('SELECT COUNT(*) FROM entries').fetchone()[0],42)
        wb=load_workbook(BytesIO(raw))
        self.assertEqual(wb['系统数据'].sheet_state,'hidden')

    def test_legacy_print_only_export_can_be_imported(self):
        wb=load_workbook(BytesIO(self.export()))
        del wb['系统数据'];del wb['系统每日记录']
        output=BytesIO();wb.save(output)
        plan=report_import.parse(output.getvalue())
        restored=report_import.apply(self.target,plan)['period_id']
        original=self.snapshot(self.source,self.pid);recovered=self.snapshot(self.target,restored)
        for name in original:
            for field in ('cur_weight','cur_count','p','points'):
                self.assertEqual(original[name][field],recovered[name][field],(name,field))

    def test_missing_only_preserves_recent_values_and_overwrite_is_explicit(self):
        plan=report_import.parse(self.export());pid=report_import.apply(self.target,plan)['period_id']
        self.target.execute('UPDATE entries SET orig_weight=9999 WHERE period_id=? AND pond_id=1',(pid,))
        report_import.apply(self.target,plan)
        self.assertEqual(self.target.execute('SELECT orig_weight FROM entries WHERE period_id=? AND pond_id=1',(pid,)).fetchone()[0],9999)
        report_import.apply(self.target,plan,overwrite=True)
        self.assertEqual(self.target.execute('SELECT orig_weight FROM entries WHERE period_id=? AND pond_id=1',(pid,)).fetchone()[0],1001)

    def test_wrong_farm_rejected(self):
        raw=self.export();token=farms.current.set('demo')
        try:
            with self.assertRaises(ValueError):report_import.parse(raw)
        finally:farms.current.reset(token)

    def test_partial_save_does_not_erase_other_fields_and_explicit_clear_works(self):
        service.save_entries(self.source,self.pid,[dict(pond_id=1,orig_weight=1234,orig_count=5678,conv_rate=.85)])
        service.save_entries(self.source,self.pid,[dict(pond_id=1,fields=['daily_feed'],daily_feed=5,orig_weight=None)],partial=True)
        row=self.source.execute('SELECT * FROM entries WHERE period_id=? AND pond_id=1',(self.pid,)).fetchone()
        self.assertEqual(row['orig_weight'],1234);self.assertEqual(row['orig_count'],5678)
        service.save_entries(self.source,self.pid,[dict(pond_id=1,fields=['orig_weight'],orig_weight=None)],partial=True)
        self.assertIsNone(self.source.execute('SELECT orig_weight FROM entries WHERE period_id=? AND pond_id=1',(self.pid,)).fetchone()[0])

    def test_deleted_period_can_restore_without_overwriting_new_data(self):
        expected=self.snapshot(self.source,self.pid)
        service.delete_period(self.source,self.pid)
        trash=service.deleted_periods(self.source)
        new=service.ensure_half(self.source,2026,11,'上')
        service.save_entries(self.source,new,[dict(pond_id=1,orig_weight=9999,category='新')])
        restored=service.restore_period(self.source,trash[0]['id'])['period_id']
        self.assertEqual(restored,new)
        self.assertEqual(self.source.execute('SELECT orig_weight FROM entries WHERE period_id=? AND pond_id=1',(new,)).fetchone()[0],9999)
        self.assertEqual(self.snapshot(self.source,new)['2#'],expected['2#'])
        self.assertEqual(service.deleted_periods(self.source),[])


if __name__=='__main__':unittest.main()
