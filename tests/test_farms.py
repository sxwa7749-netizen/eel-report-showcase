"""多厂：每个厂各用各的数据库，录入账号只能进自己的厂，老板能看全部。
运行：python -m unittest tests.test_farms（会用临时目录，不碰 data/）"""
import importlib
import os
import sys
import tempfile
import unittest

PW = {"APP_PASSWORD": "t-demo", "DEMO2_PASSWORD": "t-demo2", "DEMO3_PASSWORD": "t-demo3", "BOSS_PASSWORD": "t-boss"}


class FarmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_env = {k: os.environ.get(k) for k in [*PW, "DATA_DIR", "FARMS_ALL"]}
        os.environ.update(PW, DATA_DIR=cls.tmp.name)
        os.environ.pop("FARMS_ALL", None)
        for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
            del sys.modules[m]  # 让 app 按上面的环境变量重新加载
        from fastapi.testclient import TestClient
        cls.main = importlib.import_module("app.main")
        cls.TestClient = TestClient

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
            del sys.modules[m]
        cls.tmp.cleanup()

    def client(self, pw):
        import secrets
        token=secrets.token_hex(32)
        c = self.TestClient(self.main.app)
        r = c.post("/login", data={"password": pw,"tab_token":token}, follow_redirects=False)
        self.assertEqual(r.status_code, 303, pw)
        c.headers["X-Page-Session"]=token
        c.headers["X-CSRF-Token"] = c.get("/api/me").json()["csrf_token"]
        return c

    def test_changing_account_cannot_mix_old_page_with_new_farm(self):
        old=self.client('t-demo2');new=self.client('t-demo')
        self.assertEqual(old.get('/api/me').headers['X-Demo-Farm'],'demo2')
        self.assertEqual(new.get('/api/me').headers['X-Demo-Farm'],'demo')
        old.cookies.set('demo_session',new.cookies.get('demo_session'))
        self.assertEqual(old.get('/api/me').status_code,401)
        self.assertEqual(old.get('/api/periods/1/export').status_code,401)

    def test_independent_access_controls_block_login_and_existing_sessions(self):
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'AVAILABILITY_DIR':folder}):
            root=Path(folder)
            demo=self.client('t-demo');demo2c=self.client('t-demo2');demo3=self.client('t-demo3');boss=self.client('t-boss')
            (root/'eel-demo2.closed').write_text('fixture')
            self.assertEqual(demo2c.get('/api/me').status_code,503)
            self.assertIn('示例二',demo2c.get('/api/me').json()['detail'])
            self.assertEqual(demo2c.get('/api/periods/1/export').status_code,503)
            self.assertEqual(demo2c.get('/api/me',headers={'X-Farm':'demo'}).status_code,503)
            for client in (demo,demo3,boss):self.assertEqual(client.get('/api/me').status_code,200)
            new=self.TestClient(self.main.app)
            for _ in range(6):
                self.assertEqual(new.post('/login',data={'password':'t-demo2','tab_token':'d'*64}).status_code,503)
            (root/'eel-boss.closed').write_text('fixture')
            self.assertEqual(boss.get('/api/me').status_code,503)
            self.assertEqual(demo.get('/api/me').status_code,200)
            (root/'eel.closed').write_text('fixture')
            for client in (demo,demo2c,demo3,boss):self.assertEqual(client.get('/api/me').status_code,503)
            (root/'eel.closed').unlink()
            self.assertEqual(demo.get('/api/me').status_code,200)
            self.assertEqual(demo2c.get('/api/me').status_code,503)

    def test_each_farm_has_own_database_and_setup(self):
        demo2c = self.client("t-demo2")
        me = demo2c.get("/api/me").json()
        self.assertEqual((me["role"], me["farm"]["id"], me["ponds"]), ("admin", "demo2", 0))
        self.assertEqual(len(demo2c.post("/api/ponds/setup", json={"count": 32}).json()), 32)
        # 已经有池号就不能再生成
        self.assertEqual(demo2c.post("/api/ponds/setup", json={"count": 5}).status_code, 400)

        demo3c = self.client("t-demo3")
        self.assertEqual(demo3c.get("/api/me").json()["ponds"], 0)
        demo3c.post("/api/ponds/setup", json={"count": 12})
        self.assertEqual(len(demo3c.get("/api/ponds").json()), 12)
        self.assertEqual(len(demo2c.get("/api/ponds").json()), 32)  # 互不影响

        # 录入账号改不了自己是哪个厂：X-Farm 对录入账号无效
        self.assertEqual(len(demo3c.get("/api/ponds", headers={"X-Farm": "demo2"}).json()), 12)
        # 录入账号看不了老板端
        self.assertEqual(demo3c.get("/api/boss/all?year=2026&month=11").status_code, 403)

        self.assertTrue((self.main.DATA_DIR / "demo2.db").exists())
        self.assertTrue((self.main.DATA_DIR / "demo3.db").exists())

    def test_boss_sees_all_farms(self):
        boss = self.client("t-boss")
        me = boss.get("/api/me").json()
        self.assertEqual(me["role"], "boss")
        self.assertEqual([f["id"] for f in me["farms"]], ["demo", "demo2", "demo3"])
        all_ = boss.get("/api/boss/all?year=2026&month=11").json()["farms"]
        self.assertEqual([f["farm"]["id"] for f in all_], ["demo", "demo2", "demo3"])
        # 老板按 X-Farm 看某个厂
        r = boss.get("/api/boss?year=2026&month=11", headers={"X-Farm": "demo3"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(boss.get("/api/boss?year=2026&month=11", headers={"X-Farm": "nope"}).status_code, 400)
        # 老板不能改养殖数据
        self.assertEqual(boss.post("/api/ponds/setup", json={"count": 3}, headers={"X-Farm": "demo3"}).status_code, 403)

    def test_export_many_ponds_splits_pages_and_uses_farm_name(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from app import excel_export, farms, service
        from app.db import tx
        token = farms.current.set("demo3")
        try:
            with tx() as c:
                if not c.execute("SELECT COUNT(*) FROM ponds").fetchone()[0]:
                    service.setup_ponds(c, 12)
                existing = c.execute("SELECT COUNT(*) FROM ponds").fetchone()[0]
                c.executemany("INSERT INTO ponds(name, category, sort) VALUES(?,?,?)",
                              [(f"B{i}", "老", 100 + i) for i in range(40 - existing)])
                pid = service.create_period(c, 2026, 10, "1-15", None)
                ids = [r[0] for r in c.execute("SELECT id FROM ponds")]
                service.save_entries(c, pid, [{"pond_id": i, "orig_weight": "1000", "orig_count": "2000"} for i in ids])
                data, name = excel_export.export_period(c, pid)
        finally:
            farms.current.reset(token)
        self.assertTrue(name.startswith("示例三鳗场报表"))
        ws = load_workbook(BytesIO(data))["报表"]
        self.assertTrue(ws["A1"].value.startswith("示例三鳗场"))
        self.assertEqual(len(ws.row_breaks.brk), 2)  # 40 口池 → A4 横向每页约 14 口 → 三页
        self.assertFalse(ws.sheet_properties.pageSetUpPr.fitToPage)  # 固定比例，分页符才会生效
        self.assertGreaterEqual(ws.page_setup.scale, 90)  # 基本不缩小

    def test_wrong_password(self):
        c = self.TestClient(self.main.app)
        self.assertEqual(c.post("/login", data={"password": "nope","tab_token":"b"*64}, follow_redirects=False).status_code, 401)


if __name__ == "__main__":
    unittest.main()
