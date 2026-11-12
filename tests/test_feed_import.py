from io import BytesIO
import sqlite3
import unittest
from openpyxl import Workbook
from app import feed_import,excel_io,report_import,service
from app.db import SCHEMA


def feed_file():
    wb=Workbook();ws=wb.active;ws.title='16-31天'
    for cell,value in {'B3':'转化率%','B4':'初始重量','C2':'1#','C3':.85,'C4':670,
                       'A5':16,'C5':4.5,'C6':4.5,'A65':31,'C65':5,'C7':'=(C5+C6)/C8'}.items():
        ws[cell]=value
    ws.cell(1,16384).number_format='0.00'  # 原文件的远处空白格式，不是数据列。
    output=BytesIO();wb.save(output);return output.getvalue()


class FeedImportTests(unittest.TestCase):
    def test_wrong_entry_identifies_feed_before_print_range_check(self):
        raw=feed_file()
        for read in (lambda:report_import.parse(raw),lambda:excel_io.import_workbook(None,raw)):
            with self.assertRaises(feed_import.FeedWorkbookDetected) as error:read()
            self.assertEqual(error.exception.import_format,'feed')

    def test_month_validation_keeps_day_31_explicit(self):
        plan=feed_import.parse(feed_file(),2026,10)
        self.assertEqual(len(plan['sheets'][0]['cells']),3)
        self.assertEqual(sum(c['value'] for c in plan['sheets'][0]['cells']),14)
        with self.assertRaisesRegex(ValueError,'11月没有31日'):
            feed_import.parse(feed_file(),2026,11)

    def test_feed_apply_preserves_stock_injury_and_manual_total(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;c.executescript(SCHEMA)
        try:
            service.setup_ponds(c,1);period=service.ensure_half(c,2026,10,'下')
            c.execute("UPDATE entries SET orig_count=10000,injured=3,feed_kg=150,note='保留备注' WHERE period_id=?",(period,))
            service.save_daily(c,2026,10,'injury',[dict(day=16,shift='早',pond_id=1,value=2)])
            plan=feed_import.parse(feed_file(),2026,10)
            result=feed_import.apply(c,plan);self.assertEqual(result['daily_cells'],3)
            row=c.execute('SELECT * FROM entries WHERE period_id=?',(period,)).fetchone()
            self.assertEqual((row['orig_count'],row['injured'],row['feed_kg'],row['note']),(10000,3,150,'保留备注'))
            self.assertEqual(c.execute("SELECT value FROM daily WHERE kind='injury'").fetchone()[0],2)
        finally:c.close()
