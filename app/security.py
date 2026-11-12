"""登录限速、可撤销会话、请求边界。密码和会话原文不写日志。"""
import hashlib
import hmac
from io import BytesIO
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
import zipfile
from contextlib import contextmanager

from starlette.responses import JSONResponse


class SecurityStore:
    def __init__(self, path, signing_key, clock=time.time):
        self.path = Path(path)
        self.key = signing_key.encode()
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''
              CREATE TABLE IF NOT EXISTS login_attempts(ip TEXT PRIMARY KEY,count INTEGER NOT NULL,reset_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,role TEXT NOT NULL,farm TEXT,credential TEXT NOT NULL,expires_at REAL NOT NULL);
            ''')
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def attempt(self, ip):
        """每来源15分钟最多5次登录请求，成功后清零；重启也不会清除限制。"""
        now = self.clock()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM login_attempts WHERE reset_at<=?', (now,))
            row = c.execute('SELECT count,reset_at FROM login_attempts WHERE ip=?', (ip,)).fetchone()
            if row and row[0] >= 5:
                return max(1, int(row[1] - now) + 1)
            c.execute('INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(ip) DO UPDATE SET count=count+1', (ip, now+900))
        return 0

    def fingerprint(self, password):
        return hmac.new(self.key, password.encode(), hashlib.sha256).hexdigest()

    def issue(self, ip, role, farm, password, lifetime):
        token = secrets.token_urlsafe(32)
        with self.connect() as c:
            c.execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
            c.execute('DELETE FROM sessions WHERE expires_at<=?', (self.clock(),))
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), role, farm, self.fingerprint(password), self.clock()+lifetime))
        return token

    def valid(self, session, password):
        token = session.get('sid')
        if not isinstance(token, str) or not password:
            return False
        with self.connect() as c:
            row = c.execute('SELECT role,farm,credential,expires_at FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return bool(row and row[0] == session.get('role') and row[1] == session.get('farm')
                    and row[3] > self.clock() and hmac.compare_digest(row[2], self.fingerprint(password)))

    def revoke(self, session):
        token = session.get('sid', '')
        with self.connect() as c:
            c.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(str(token).encode()).hexdigest(),))


def same_origin(request):
    # 没有 Origin 的非浏览器调用仍须提供会话内的 CSRF token。
    origin = request.headers.get('origin')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        return False
    if not origin:
        return True
    parsed = urlsplit(origin)
    # 使用明确的公网来源，避免代理改写内部scheme/Host后误拦正常浏览器。
    expected = urlsplit(os.environ.get('PUBLIC_ORIGIN') or str(request.url))
    return parsed.scheme == expected.scheme and parsed.netloc == expected.netloc


def valid_csrf(request):
    actual, expected = request.headers.get('x-csrf-token', ''), request.session.get('csrf', '')
    return bool(expected and same_origin(request) and hmac.compare_digest(actual, expected))


def page_token_hash(token):
    if not isinstance(token,str) or len(token)!=64 or any(c not in '0123456789abcdef' for c in token):
        return None
    return hashlib.sha256(token.encode()).hexdigest()


def valid_page_session(request):
    actual=page_token_hash(request.headers.get('x-page-session',''))
    expected=request.session.get('page_token_hash','')
    return bool(actual and isinstance(expected,str) and hmac.compare_digest(actual,expected))


def validate_xlsx(data):
    """拒绝压缩炸弹和过大的 XML 内容，在 Excel 解析器分配内存之前检查。"""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            files = archive.infolist()
            if len(files) > 5000 or sum(f.file_size for f in files) > 64*1024*1024:
                raise ValueError('表格解压后过大，请拆分文件再导入')
            if any(f.file_size > 16*1024*1024 or f.flag_bits & 1 for f in files):
                raise ValueError('表格内容过大或已加密，无法导入')
            if any(f.file_size > 1024*1024 and f.file_size > max(f.compress_size, 1)*500 for f in files):
                raise ValueError('表格压缩比例异常，无法导入')
            if '[Content_Types].xml' not in archive.namelist():
                raise ValueError('请上传有效的 .xlsx 文件')
    except zipfile.BadZipFile:
        raise ValueError('请上传有效的 .xlsx 文件')


def check_workbook(workbook):
    if any(sheet.max_row>20000 or sheet.max_column>2048 for sheet in workbook):
        raise ValueError('表格范围过大，请清理远处的空白格式后再导入')


class BodyTooLarge(Exception):
    pass


class SecurityBoundary:
    def __init__(self, app, allow_blob_images=False):
        self.app = app
        self.allow_blob_images = allow_blob_images

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope.get('path', '')
        limit = 16*1024 if path == '/login' else (12*1024*1024 if path.startswith('/api/import') else 2*1024*1024)
        headers = dict(scope.get('headers', []))
        raw_length = headers.get(b'content-length', b'0')
        try:
            too_large = int(raw_length) < 0 or int(raw_length) > limit
        except ValueError:
            too_large = True
        if too_large:
            return await JSONResponse({'detail': '请求内容太大'}, status_code=413)(scope, receive, send)
        seen, started = 0, False

        async def bounded_receive():
            nonlocal seen
            message = await receive()
            if message['type'] == 'http.request':
                seen += len(message.get('body', b''))
                if seen > limit:
                    raise BodyTooLarge()
            return message

        async def secured_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                h = list(message.get('headers', []))
                h.extend([
                    (b'x-content-type-options', b'nosniff'),
                    (b'x-frame-options', b'DENY'),
                    (b'referrer-policy', b'same-origin'),
                    (b'permissions-policy', b'camera=(), microphone=(), geolocation=()'),
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                ])
                if self.allow_blob_images:
                    h=[(k,v.replace(b"img-src 'self' data:",b"img-src 'self' data: blob:")) if k==b'content-security-policy' else (k,v) for k,v in h]
                if not path.startswith('/static/'):
                    h = [(k, v) for k, v in h if k.lower() != b'cache-control']
                    h.append((b'cache-control', b'no-store'))
                message['headers'] = h
            await send(message)
        try:
            await self.app(scope, bounded_receive, secured_send)
        except BodyTooLarge:
            if started:
                raise
            await JSONResponse({'detail': '请求内容太大'}, status_code=413)(scope, receive, secured_send)
