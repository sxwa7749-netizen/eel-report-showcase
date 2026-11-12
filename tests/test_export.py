"""导出 Excel：转化率按实际值显示；没有数据的池不进报表"""
import sqlite3
import unittest
from io import BytesIO

from openpyxl import load_workbook

from app.db import SCHEMA
from app import excel_export, service


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        self.c.executemany("INSERT INTO ponds(id, name, category, sort) VALUES (?,?,?,?)",
                           [(1, '1#', '老', 1), (2, '2#', '新', 2), (3, '3#', '新', 3), (4, '4#', '暂无', 4)])
        self.pid = service.create_period(self.c, 2026, 11, '1-15', None)
        service.save_entries(self.c, self.pid, [
            {'pond_id': 1, 'category': '老', 'orig_weight': '1000', 'orig_count': '2000', 'conv_rate': '0.855', 'feed_kg': '100'},
            {'pond_id': 2, 'category': '新', 'orig_weight': '500', 'orig_count': '1000', 'conv_rate': '0.85'},
            {'pond_id': 3, 'category': '新', 'conv_rate': '0.8'},   # 只有转化率，没数据
            {'pond_id': 4, 'category': '暂无'},                      # 空池
        ])
        data, _ = excel_export.export_period(self.c, self.pid)
        self.wb = load_workbook(BytesIO(data))

    def tearDown(self):
        self.c.close()

    def find(self, ws, text):
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith(text):
                    return cell
        return None

    def test_conv_rate_not_rounded(self):
        self.assertEqual(excel_export.PCT1(0.85), "0%")
        self.assertEqual(excel_export.PCT1(0.855), "0.0%")
        self.assertEqual(excel_export.PCT1(0.8525), "0.00%")
        ws = self.wb["报表竖版"]
        conv = [c for row in ws.iter_rows() for c in row if isinstance(c.value, float) and abs(c.value - 0.855) < 1e-9]
        self.assertEqual(len(conv), 1)
        self.assertEqual(conv[0].number_format, "0.0%")

    def test_empty_ponds_hidden(self):
        ws = self.wb["报表"]
        heads = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and '#\n' in c.value]
        self.assertEqual(heads, ['1#\n老', '2#\n新'])
        names = [c.value for c in self.wb["报表竖版"]["A"] if isinstance(c.value, str) and c.value.endswith('#')]
        self.assertEqual(names, ['1#', '2#'])
        # 汇总里的池数按有数据的池算
        total_label = self.find(ws, '总计')
        self.assertEqual(ws.cell(total_label.row, total_label.column + 1).value, 2)


if __name__ == '__main__':
    unittest.main()
