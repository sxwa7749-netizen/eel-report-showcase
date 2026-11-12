import importlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from app.security import SecurityStore, validate_xlsx


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.store = SecurityStore(Path(self.tmp.name)/'security.sqlite3', 'test-key', lambda:self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rate_limit_survives_restart_and_expires(self):
        for _ in range(5):
            self.assertEqual(self.store.attempt('192.0.2.1'), 0)
        restarted = SecurityStore(self.store.path, 'test-key', lambda:self.now)
        self.assertGreater(restarted.attempt('192.0.2.1'), 0)
        self.assertEqual(restarted.attempt('192.0.2.2'), 0)
        self.now += 901
        self.assertEqual(restarted.attempt('192.0.2.1'), 0)

    def test_linux_deployment_assets_use_lf(self):
        root=Path(__file__).resolve().parent.parent/'deploy'
        for pattern in ('*.sh','*.service','*.timer'):
            for path in root.glob(pattern):
                self.assertNotIn(b'\r',path.read_bytes(),str(path))

    def test_absolute_expiry_logout_and_password_rotation(self):
        sid = self.store.issue('127.0.0.1', 'boss', None, 'password', 60)
        session = dict(sid=sid, role='boss')
        self.assertTrue(self.store.valid(session, 'password'))
        self.assertFalse(self.store.valid(dict(session, role='admin'), 'password'))
        self.assertFalse(self.store.valid(session, 'new-password'))
        self.now += 61
        self.assertFalse(self.store.valid(session, 'password'))
        sid = self.store.issue('127.0.0.1', 'boss', None, 'password', 60)
        session['sid'] = sid
        self.store.revoke(session)
        self.assertFalse(self.store.valid(session, 'password'))

    def test_compression_bomb_is_rejected_before_excel_parser(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml', '<Types/>')
            z.writestr('xl/bomb.xml', 'x'*(2*1024*1024))
        with self.assertRaises(ValueError):
            validate_xlsx(data.getvalue())

    def test_export_text_is_not_executable_formula_and_numbers_are_finite(self):
        from openpyxl import Workbook, load_workbook
        from app.excel_export import _cell
        from app.service import num
        workbook=Workbook()
        _cell(workbook.active,1,1,'=HYPERLINK("https://evil.invalid","click")')
        output=io.BytesIO();workbook.save(output)
        value=load_workbook(io.BytesIO(output.getvalue())).active['A1']
        self.assertNotEqual(value.data_type,'f')
        for text in ('NaN','Infinity','-Infinity'):
            with self.assertRaises(ValueError):
                num(text)


class SecurityHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        configured = dict(APP_PASSWORD='admin-security-test', BOSS_PASSWORD='boss-security-test',
                          DATA_DIR=cls.tmp.name, COOKIE_SECURE='1', COOKIE_PATH='/', ALLOWED_HOSTS='testserver',
                          DEMO2_PASSWORD='', DEMO3_PASSWORD='', REQUIRE_PASSWORD='1',PUBLIC_ORIGIN='https://testserver')
        cls.old = {k:os.environ.get(k) for k in configured}
        os.environ.update(configured)
        for name in list(sys.modules):
            if name == 'app' or name.startswith('app.'):
                del sys.modules[name]
        cls.main = importlib.import_module('app.main')
        from fastapi.testclient import TestClient
        cls.client_type = TestClient

    @classmethod
    def tearDownClass(cls):
        for k,v in cls.old.items():
            if v is None:
                os.environ.pop(k,None)
            else:
                os.environ[k]=v
        for name in list(sys.modules):
            if name == 'app' or name.startswith('app.'):
                del sys.modules[name]
        cls.tmp.cleanup()

    def client(self, password='admin-security-test'):
        c = self.client_type(self.main.app, base_url='https://testserver')
        r = c.post('/login', data={'password':password,'tab_token':'a'*64}, follow_redirects=False)
        self.assertEqual(r.status_code,303)
        c.headers['X-Page-Session']='a'*64
        return c,r

    def test_secure_cookie_csrf_and_cross_origin(self):
        c,r=self.client()
        cookie=r.headers['set-cookie']
        self.assertIn('; secure',cookie.lower())
        self.assertIn('; httponly',cookie.lower())
        self.assertIn('demo_session=',cookie)
        body=dict(year=2026,month=11,kind='feed',cells=[])
        self.assertEqual(c.put('/api/daily',json=body).status_code,403)
        token=c.get('/api/me').json()['csrf_token']
        self.assertEqual(c.put('/api/daily',json=body,headers={'X-CSRF-Token':token,'Origin':'https://evil.invalid'}).status_code,403)
        self.assertEqual(c.put('/api/daily',json=body,headers={'X-CSRF-Token':token,'Origin':'https://testserver'}).status_code,200)

    def test_admin_and_boss_require_page_proof_and_session_only_cookie(self):
        for password in ('admin-security-test','boss-security-test'):
            c,response=self.client(password)
            cookie=response.headers['set-cookie'].lower()
            self.assertNotIn('max-age',cookie);self.assertNotIn('expires=',cookie)
            self.assertEqual(c.get('/api/me').status_code,200)
            del c.headers['X-Page-Session']
            self.assertEqual(c.get('/api/me').status_code,401)
            self.assertEqual(c.get('/api/me',headers={'X-Page-Session':'f'*64}).status_code,401)
            c.headers['X-Page-Session']='a'*64
            self.assertEqual(c.get('/api/me').status_code,200)
        client=self.client_type(self.main.app,base_url='https://testserver')
        self.assertEqual(client.post('/login',data={'password':'admin-security-test'}).status_code,400)

    def test_feed_file_wrong_entry_returns_actionable_format_without_writing(self):
        from tests.test_feed_import import feed_file
        client,_=self.client();client.headers['X-CSRF-Token']=client.get('/api/me').json()['csrf_token']
        before=client.get('/api/periods').json()
        for path in ('/api/import','/api/import/report'):
            result=client.post(path,files={'file':('daily.xlsx',feed_file(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
            self.assertEqual(result.status_code,400)
            self.assertEqual(result.json()['import_format'],'feed')
        self.assertEqual(client.get('/api/periods').json(),before)

    def test_public_origin_behind_proxy(self):
        from starlette.requests import Request
        request=Request({'type':'http','method':'POST','scheme':'http','path':'/login',
                         'server':('127.0.0.1',8100),'headers':[(b'host',b'127.0.0.1:8100'),
                         (b'origin',b'https://testserver'),(b'sec-fetch-site',b'same-origin')]})
        self.assertTrue(self.main.security.same_origin(request))
        request.scope['headers']=[(b'origin',b'null')]
        self.assertFalse(self.main.security.same_origin(Request(request.scope)))

    def test_report_import_preview_partial_save_and_recoverable_delete(self):
        c,_=self.client()
        c.headers['X-CSRF-Token']=c.get('/api/me').json()['csrf_token']
        if not c.get('/api/ponds').json():
            self.assertEqual(c.post('/api/ponds/setup',json={'count':2}).status_code,200)
        p=dict(year=2027,month=3,day_range='1-15')
        pid=c.post('/api/periods',json=p).json()['id']
        pond=c.get('/api/ponds').json()[0]['id']
        patch=dict(patch=True,period=p,rows=[dict(pond_id=pond,fields=['orig_weight','orig_count','conv_rate'],orig_weight=100,orig_count=1000,conv_rate=.85)])
        self.assertEqual(c.put(f'/api/periods/{pid}/entries',json=patch).status_code,200)
        self.assertEqual(c.put(f'/api/periods/{pid}/entries',json={'rows':[]}).status_code,409)
        raw=c.get(f'/api/periods/{pid}/export').content
        count=len(c.get('/api/periods').json())
        preview=c.post('/api/import/report',files={'file':('report.xlsx',raw)},data={'preview_only':'true'})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(len(c.get('/api/periods').json()),count)
        restored=c.post('/api/import/report',files={'file':('report.xlsx',raw)},data={'preview_only':'false'})
        self.assertEqual(restored.status_code,200,restored.text)
        self.assertEqual(c.request('DELETE',f'/api/periods/{pid}',json={'period':dict(p,month=4)}).status_code,409)
        self.assertEqual(c.request('DELETE',f'/api/periods/{pid}',json={'period':p}).status_code,200)
        trash=c.get('/api/deleted-periods').json()
        self.assertTrue(trash)
        recovered=c.post(f"/api/deleted-periods/{trash[0]['id']}/restore")
        self.assertEqual(recovered.status_code,200,recovered.text)

    def test_logout_invalidates_copied_cookie(self):
        c,_=self.client()
        copied=c.cookies.get('demo_session')
        self.assertEqual(c.get('/api/me').status_code,200)
        c.get('/logout',follow_redirects=False)
        other=self.client_type(self.main.app,base_url='https://testserver')
        self.assertEqual(other.get('/api/me',headers={'Cookie':'demo_session='+copied}).status_code,401)

    def test_legacy_cookie_oversize_body_and_headers(self):
        c=self.client_type(self.main.app,base_url='https://testserver')
        self.assertEqual(c.get('/api/me',headers={'Cookie':'session=old-cookie'}).status_code,401)
        r=c.get('/login')
        self.assertIn('<meta name="referrer" content="same-origin">',r.text)
        self.assertEqual(r.headers['x-frame-options'],'DENY')
        self.assertEqual(r.headers['cache-control'],'no-store')
        self.assertIn("script-src 'self'",r.headers['content-security-policy'])
        self.assertEqual(c.post('/login',content=b'x'*17000).status_code,413)
        self.assertEqual(c.get('/login',headers={'Host':'evil.invalid'}).status_code,400)

    def test_permissions_still_separate(self):
        boss,_=self.client('boss-security-test')
        token=boss.get('/api/me').json()['csrf_token']
        self.assertEqual(boss.put('/api/daily',json={},headers={'X-CSRF-Token':token}).status_code,403)
        admin,_=self.client()
        self.assertEqual(admin.get('/api/payroll/smart?year=2026&month=11').status_code,403)


if __name__ == '__main__':
    unittest.main()
