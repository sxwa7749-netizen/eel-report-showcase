import hmac
import os
import secrets
import re
import logging
import json
import time
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import excel_export, excel_io, farms, feed_import, payroll, payroll_smart, report_import, security, service,availability
from .client_info import device_type
from .db import BASE_DIR, DATA_DIR, init_all, tx

STATIC_DIR = BASE_DIR / "static"
# 设置了 APP_PASSWORD 才需要登录（本地试用可以不设）
# APP_PASSWORD：示例的养殖录入（管理端）。其他厂各自的录入密码见 farms.py。
# BOSS_PASSWORD：老板端，能看全部厂。
PASSWORD = os.environ.get("APP_PASSWORD", "")
STAFF_PASSWORD = os.environ.get("STAFF_PASSWORD", "")
BOSS_PASSWORD = os.environ.get("BOSS_PASSWORD", "")
SESSION_SECONDS = 12 * 3600
SECURE_COOKIES = os.environ.get("COOKIE_SECURE", os.environ.get("REQUIRE_PASSWORD", "0")) == "1"
COOKIE_PATH = os.environ.get("COOKIE_PATH", "/eel/" if SECURE_COOKIES else "/")
audit_log = logging.getLogger("uvicorn.error")
if os.environ.get("REQUIRE_PASSWORD") == "1" and not PASSWORD:
    # 服务器上必须设密码，否则报表会对公网完全公开
    raise SystemExit("服务器模式必须设置 APP_PASSWORD（见 .env），拒绝启动")
_all_pw = [p for p in [BOSS_PASSWORD, *(farms.password(f["id"]) for f in farms.FARMS)] if p]
if len(_all_pw) != len(set(_all_pw)):
    raise SystemExit("各厂录入密码和老板密码不能有重复（见 .env），拒绝启动")


def _role(request: Request):
    if not PASSWORD:
        return "admin"
    role = request.session.get("role")
    if not request.session.get('page_token_hash') or request.url.path.startswith('/api/') and not security.valid_page_session(request):
        return None
    fid = request.session.get("farm")
    credential = BOSS_PASSWORD if role == "boss" else farms.password(fid) if role == "admin" and fid in farms.enabled_ids() else ""
    return role if auth_store.valid(request.session, credential) else None


_auto_done = set()  # 已经检查过的 (厂, 年, 月, 上/下半月)，同一个半月只查一次


def _auto_half(fid):
    from datetime import datetime
    now = datetime.now(service.CN_TZ)
    key = (fid, now.year, now.month, now.day <= 15)
    if key in _auto_done:
        return
    try:
        with tx(fid) as c:
            if service.ensure_current_half(c, now):
                _auto_done.add(key)
    except Exception:  # 自动开始失败不影响正常打开页面，下次请求再试
        pass


def _farm(request: Request, role):
    """这次请求属于哪个厂。录入账号固定是登录时的厂；老板端由页面用 X-Farm 指定。"""
    ids = farms.enabled_ids()
    if role == "admin" and PASSWORD:
        fid = request.session.get("farm") or farms.DEFAULT  # 多厂之前登录的旧会话＝示例
    else:  # 老板端；本地不设密码试用时也允许切厂
        fid = request.headers.get("x-farm") or farms.DEFAULT
    return fid if fid in ids else None


def _secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        if os.name != "nt":
            f.chmod(0o600)
    return f.read_text().strip()


session_key = _secret_key()
auth_store = security.SecurityStore(DATA_DIR / "security.sqlite3", session_key)
app = FastAPI(title="鳗场报表", docs_url=None, redoc_url=None, openapi_url=None)
init_all()


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in ("/login", "/boss-login", "/logout") or path.startswith("/static/"):
        return await call_next(request)
    role = _role(request)
    if role not in ("admin", "boss"):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        return RedirectResponse("login", status_code=303)
    fid = _farm(request, role)
    if fid is None:
        return JSONResponse({"detail": "这个厂还没启用"}, status_code=400)
    paused=availability.paused_system(role,fid)
    if paused:
        audit_log.info('DEMO_MONITOR %s',json.dumps(dict(ts=time.time(),farm=fid if role=='admin' else 'all',role=role,
            ip=request.client.host if request.client else '',method=request.method,path='/eel'+path,status=503,
            device=device_type(request.headers.get('user-agent')),duration=0),separators=(',',':')))
        return availability.paused_response(paused,path.startswith('/api/'))
    if path.startswith("/api/"):
        money_path = path in ("/api/payroll", "/api/payroll/carry", "/api/payroll/smart", "/api/payroll/employees", "/api/payroll/events", "/api/payroll/settle", "/api/payroll/settle-all", "/api/payroll/reopen", "/api/payroll/upgrade", "/api/payroll/override") or bool(re.fullmatch(r"/api/payroll/(?:events/|employees/)?\d+", path))
        if role == "boss":
            allowed = (request.method == "GET" and path in ("/api/me", "/api/boss", "/api/boss/all", "/api/payroll", "/api/payroll/smart")) or (money_path and request.method in ("POST", "DELETE"))
            if not allowed:
                return JSONResponse({"detail": "老板端仅查看经营数据，不能修改养殖记录"}, status_code=403)
        elif money_path or path in ("/api/boss", "/api/boss/all"):
            return JSONResponse({"detail": "请使用老板端登录查看经营概况和工资"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and not security.valid_csrf(request):
            return JSONResponse({"detail": "页面验证已失效，请刷新页面重新操作"}, status_code=403)
    # 后面的接口都用这个厂的数据库（tx() 读取 farms.current）
    token = farms.current.set(fid)
    if path.startswith("/api/") and request.method == "GET":
        _auto_half(fid)  # 到了 1 号 / 16 号，自动开始新的半月期报
    try:
        monitor_started = time.time()
        response = await call_next(request)
        response.headers['X-Demo-Farm']=fid
        # 仅记录访问元数据；不记录请求参数、正文、Cookie或业务数值。
        audit_log.info('DEMO_MONITOR %s', json.dumps(dict(
            ts=monitor_started, farm=fid if role == 'admin' else 'all', role=role,
            ip=request.client.host if request.client else '', method=request.method,
            path='/eel'+path, status=response.status_code,device=device_type(request.headers.get('user-agent')),
            duration=max(0,time.time()-monitor_started)), separators=(',',':')))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            audit_log.info("write role=%s farm=%s method=%s path=%r status=%d", role, fid, request.method, path, response.status_code)
        return response
    finally:
        farms.current.reset(token)


class CachedStatic(StaticFiles):
    """带版本号（?v=）的静态文件长期缓存，手机再次打开不用重新下载。"""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if b"v=" in scope.get("query_string", b""):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


# 后加的中间件在外层：session 要先于登录检查执行；gzip 在最外层，压缩所有较大的响应
app.add_middleware(SessionMiddleware, secret_key=session_key, session_cookie="demo_session", max_age=None, same_site="lax", https_only=SECURE_COOKIES, path=COOKIE_PATH)
app.add_middleware(GZipMiddleware, minimum_size=800, compresslevel=6)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()])
app.add_middleware(security.SecurityBoundary)
app.mount("/static", CachedStatic(directory=STATIC_DIR), name="static")


@app.exception_handler(ValueError)
async def value_error(_, exc):
    body={"detail":str(exc)}
    if isinstance(exc,feed_import.FeedWorkbookDetected):body['import_format']='feed'
    return JSONResponse(body,status_code=400)


@app.exception_handler(security.BodyTooLarge)
async def too_large(_, exc):
    return JSONResponse({"detail": "请求内容太大"}, status_code=413)


@app.exception_handler(LookupError)
async def not_found(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=404)


# ---------- 页面 & 登录 ----------

@app.get("/")
def index(request: Request):
    page=(STATIC_DIR/'index.html').read_text(encoding='utf-8').replace('data-session-required="1"',f'data-session-required="{int(bool(PASSWORD))}"')
    return HTMLResponse(page,headers={'Cache-Control':'no-store'})


@app.get("/boss")
def boss_index(request: Request):
    return RedirectResponse("./", status_code=303)


LOGIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="referrer" content="same-origin">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>登录 · 鳗场报表</title>
<link rel="stylesheet" href="static/style.css?v=34"></head><body class="login-page">
<form method="post" action="login" class="login-card">
<div class="brand-mark">{mark}</div>
<h1>{title}</h1><p class="sub">{intro}</p>{err}
<label>密码<input type="password" name="password" autofocus autocomplete="current-password" placeholder="输入管理员密码"></label>
<button class="btn cta" type="submit">登录</button></form><script src="static/page-session.js?v=1" data-session-scope="eel" data-login-page></script></body></html>"""


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _login_html()


def _login_html(err=""):
    # 只有示例一个厂时保持原样；多厂后登录页不显示具体厂名（输入哪个厂的密码就进哪个厂）
    one = len(farms.enabled()) == 1
    return LOGIN_HTML.format(err=err, intro="请输入密码登录", mark="一" if one else "鳗",
                             title="示例鳗场" if one else "鳗场报表").replace('placeholder="输入管理员密码"', 'placeholder="输入密码"')


@app.get("/boss-login", response_class=HTMLResponse)
def boss_login_page():
    return RedirectResponse("login", status_code=303)


@app.post("/login")
def login(request: Request, password: str = Form(""),tab_token:str=Form('')):
    if not security.same_origin(request):
        return HTMLResponse(_login_html(err='<p class="err">请从本站登录页面提交</p>'), status_code=403)
    page_hash=security.page_token_hash(tab_token)
    if not page_hash:
        return HTMLResponse(_login_html(err='<p class="err">请刷新登录页面后重新输入密码</p>'),status_code=400)
    ip = request.client.host if request.client else "unknown"
    retry = auth_store.attempt(ip)
    if retry:
        return HTMLResponse(_login_html(err='<p class="err">登录尝试过多，请15分钟后重试</p>'), status_code=429, headers={"Retry-After": str(retry)})
    pw = password.encode()
    role = farm = None
    for f in farms.enabled():  # 各厂的录入密码 → 进入对应的厂
        p = farms.password(f["id"])
        if p and hmac.compare_digest(pw, p.encode()):
            role, farm = "admin", f["id"]
    if not role and BOSS_PASSWORD and hmac.compare_digest(pw, BOSS_PASSWORD.encode()):
        role = "boss"
    if role:
        paused=availability.paused_system(role,farm)
        if paused:
            with auth_store.connect() as c:c.execute('DELETE FROM login_attempts WHERE ip=?',(ip,))
            audit_log.info('DEMO_MONITOR %s',json.dumps(dict(ts=time.time(),farm=farm if role=='admin' else 'all',role=role,
                ip=ip,method='POST',path='/eel/login',status=503,device=device_type(request.headers.get('user-agent')),duration=0),separators=(',',':')))
            return availability.paused_response(paused)
        request.session.clear()
        request.session["role"] = role
        if farm:
            request.session["farm"] = farm
        credential = farms.password(farm) if farm else BOSS_PASSWORD
        request.session["sid"] = auth_store.issue(ip, role, farm, credential, SESSION_SECONDS)
        request.session["csrf"] = secrets.token_urlsafe(32)
        request.session['page_token_hash']=page_hash
        audit_log.info('DEMO_MONITOR %s',json.dumps(dict(ts=time.time(),farm=farm if role=='admin' else 'all',
            role=role,ip=ip,method='POST',path='/eel/login',status=303,
            device=device_type(request.headers.get('user-agent')),duration=0),separators=(',',':')))
        return RedirectResponse("./", status_code=303)
    audit_log.warning("login_rejected source=%r", ip)
    return HTMLResponse(_login_html(err='<p class="err">密码不对，请重试</p>'), status_code=401)


@app.get("/logout")
def logout(request: Request):
    if not security.same_origin(request):
        return JSONResponse({"detail": "请从本站退出登录"}, status_code=403)
    role=_role(request)
    if role in ('admin','boss'):
        audit_log.info('DEMO_MONITOR %s',json.dumps(dict(ts=time.time(),farm=request.session.get('farm') if role=='admin' else 'all',
            role=role,ip=request.client.host if request.client else '',method='GET',path='/eel/logout',status=303,
            device=device_type(request.headers.get('user-agent')),duration=0),separators=(',',':')))
    auth_store.revoke(request.session)
    request.session.clear()
    return RedirectResponse("login", status_code=303)


@app.get("/api/me")
def me(request: Request):
    """当前登录身份、所在的厂、这个厂有没有池号（没有就让管理端先填池数）"""
    with tx() as c:
        ponds = c.execute("SELECT COUNT(*) FROM ponds").fetchone()[0]
    request.session.setdefault("csrf", secrets.token_urlsafe(32))
    return {"login_required": bool(PASSWORD), "role": _role(request), "csrf_token": request.session["csrf"], "farm": farms.public(),
            "farms": [farms.public(f["id"]) for f in farms.enabled()], "ponds": ponds}


@app.get("/api/payroll")
def get_payroll(year: int, month: int):
    with tx() as c:
        return payroll.listing(c, year, month)


@app.get("/api/payroll/smart")
def smart_payroll(year: int, month: int):
    with tx() as c:
        return payroll_smart.overview(c, year, month)


@app.post("/api/payroll/employees")
async def save_employee(request: Request):
    body = await request.json()
    with tx() as c:
        return payroll_smart.save_employee(c, body)


@app.delete("/api/payroll/employees/{eid}")
def delete_employee(eid: int):
    """只能删除还没有任何工资记录的员工；有记录的请设为离职。"""
    with tx() as c:
        return payroll_smart.delete_employee(c, eid)


@app.post("/api/payroll/events")
async def save_wage_event(request: Request):
    body = await request.json()
    with tx() as c:
        return payroll_smart.save_event(c, body)


@app.delete("/api/payroll/events/{rid}")
def delete_wage_event(rid: int):
    with tx() as c:
        payroll_smart.delete_event(c, rid)
    return {"ok": True}


@app.post("/api/payroll/settle")
async def settle_payroll(request: Request):
    body = await request.json()
    with tx() as c:
        return payroll_smart.settle(c, body)


@app.post("/api/payroll/settle-all")
async def settle_all_payroll(request: Request):
    """一键确认多名员工已发放；任一人金额有变化则整批不发。"""
    body = await request.json()
    with tx() as c:
        return payroll_smart.settle_all(c, body)


@app.post("/api/payroll/reopen")
async def reopen_payroll(request: Request):
    body = await request.json()
    with tx() as c:
        return payroll_smart.reopen(c, body)


@app.post("/api/payroll/override")
async def override_payroll(request: Request):
    """本月手动改数：月薪、请假扣款；留空表示恢复自动计算。"""
    body = await request.json()
    with tx() as c:
        return payroll_smart.save_override(c, body)


@app.post("/api/payroll/upgrade")
async def upgrade_legacy_payroll(request: Request):
    body = await request.json()
    with tx() as c:
        return payroll_smart.upgrade_legacy(c, body.get("id"))


@app.post("/api/payroll")
async def save_payroll(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("工资内容不正确")
    with tx() as c:
        return payroll.save(c, body)


@app.post("/api/payroll/carry")
async def carry_payroll(request: Request):
    body = await request.json()
    year, month = int(body.get("year", 0)), int(body.get("month", 0))
    payroll.period(year, month)
    prev_year, prev_month = (year, month - 1) if month > 1 else (year - 1, 12)
    with tx() as c:
        existing = {r["name"] for r in payroll.listing(c, year, month)["rows"]}
        added = 0
        for row in c.execute("SELECT name,salary_cents FROM payroll WHERE year=? AND month=?", (prev_year, prev_month)).fetchall():
            key = f"{year:04d}-{month:02d}"
            if c.execute("SELECT 1 FROM wage_employees WHERE name=? AND start_month<=? AND (end_month IS NULL OR end_month>=?)", (row["name"], key, key)).fetchone():
                continue
            if row["name"] not in existing:
                c.execute("INSERT INTO payroll(year,month,name,salary_cents) VALUES(?,?,?,?)", (year, month, row["name"], row["salary_cents"]))
                added += 1
        return {**payroll.listing(c, year, month), "added": added}


@app.delete("/api/payroll/{rid}")
def delete_payroll(rid: int):
    with tx() as c:
        if c.execute("SELECT 1 FROM wage_legacy_promotions WHERE payroll_id=?", (rid,)).fetchone():
            raise ValueError("这条记录已切换到自动台账，原记录仅作历史保留，不能删除")
        row = c.execute("SELECT paid FROM payroll WHERE id=?", (rid,)).fetchone()
        if not row:
            raise LookupError("工资记录不存在")
        if row["paid"]:
            raise ValueError("已发放记录不能删除，请先改为未发放")
        c.execute("DELETE FROM payroll WHERE id=?", (rid,))
    return {"ok": True}


@app.get("/api/boss")
def get_boss(year: int, month: int):
    payroll.period(year, month)
    with tx() as c:
        halves = {}
        for half in ("上", "下"):
            info = service.half_info(c, year, month, half)
            halves[half] = service.period_report(c, info["id"]) if info["id"] else None
        latest = halves["下"] or halves["上"]
        feed = c.execute("SELECT SUM(value) FROM daily WHERE year=? AND month=? AND kind='feed'", (year, month)).fetchone()[0]
        return {"halves": halves, "latest": latest, "monthly_feed": feed, "today": service.today_feed(c, year, month)}


@app.get("/api/boss/all")
def get_boss_all(year: int, month: int):
    """老板端「全部」：每个厂的库存、尾数、投喂、今天漏喂、当月应发工资（各厂分别算，前端再合计）"""
    payroll.period(year, month)
    out = []
    for f in farms.enabled():
        _auto_half(f["id"])
        with tx(f["id"]) as c:
            report = None
            for half in ("下", "上"):  # 当月最新一期期报；当月还没有就用上个月的
                info = service.half_info(c, year, month, half)
                if info["id"]:
                    report = service.period_report(c, info["id"])
                    break
            if report is None:
                py, pm = (year, month - 1) if month > 1 else (year - 1, 12)
                for half in ("下", "上"):
                    info = service.half_info(c, py, pm, half)
                    if info["id"]:
                        report = service.period_report(c, info["id"])
                        break
            feed = c.execute("SELECT SUM(value) FROM daily WHERE year=? AND month=? AND kind='feed'", (year, month)).fetchone()[0]
            pay = payroll_smart.overview(c, year, month)
            ponds = c.execute("SELECT COUNT(*) FROM ponds WHERE active=1").fetchone()[0]
            out.append({
                "farm": farms.public(f["id"]), "ponds": ponds,
                "period": report and {k: report["period"][k] for k in ("year", "month", "day_range", "day_to")},
                "totals": report and report["totals"],
                "monthly_feed": feed, "alert": service.feed_alert(c),
                "pay_total_cents": pay["total_cents"], "pay_people": len(pay["rows"]) + len(pay["legacy"]["rows"]),
            })
    return {"farms": out}


@app.get("/api/feed-alert")
def get_feed_alert():
    """管理端首页：今天哪些池早班/晚班漏喂或喂 0（北京时间）。"""
    with tx() as c:
        return service.feed_alert(c)


# ---------- 池号 ----------

@app.get("/api/ponds")
def get_ponds():
    with tx() as c:
        return service.list_ponds(c)


@app.post("/api/ponds/setup")
async def setup_ponds(request: Request):
    """新厂第一次使用：按池数生成 1#、2#…（之后可在池号管理里改名、分新老鳗、停用）"""
    body = await request.json()
    with tx() as c:
        return service.setup_ponds(c, body.get("count"), body.get("category") or "新")


@app.put("/api/ponds")
async def put_ponds(request: Request):
    body = await request.json()
    with tx() as c:
        service.save_ponds(c, body["ponds"])
        return service.list_ponds(c)


# ---------- 报表期间 ----------

@app.get("/api/periods")
def get_periods():
    with tx() as c:
        return service.list_periods(c)


@app.post("/api/periods")
async def post_period(request: Request):
    b = await request.json()
    with tx() as c:
        pid = service.create_period(c, int(b["year"]), int(b["month"]), b["day_range"], b.get("carry_from"))
        return {"id": pid}


@app.get("/api/half")
def get_half(year: int, month: int, half: str):
    """某年某月上/下半月对应的期间（没有就返回 id=null，不自动创建）。"""
    with tx() as c:
        return service.half_info(c, year, month, half)


@app.get("/api/half/report")
def get_half_report(year: int, month: int, half: str):
    """半月信息 + 期报，一次请求拿全（少一次网络往返）。"""
    with tx() as c:
        info = service.half_info(c, year, month, half)
        return {"info": info, "report": service.period_report(c, info["id"]) if info["id"] else None}


@app.post("/api/half")
async def post_half(request: Request):
    """开始录入某个半月：没有就新建，并从上一期带入原重量、原尾数。"""
    b = await request.json()
    with tx() as c:
        pid = service.ensure_half(c, int(b["year"]), int(b["month"]), b["half"])
        return service.half_info(c, int(b["year"]), int(b["month"]), b["half"]) | {"id": pid}


@app.get("/api/dashboard")
def get_dashboard(date: str):
    with tx() as c:
        return service.dashboard(c, date)


@app.delete("/api/periods/{pid}")
async def del_period(pid: int, request:Request):
    body=await request.json()
    with tx() as c:
        actual=service.get_period(c,pid)
        if body.get('period')!={key:actual[key] for key in ('year','month','day_range')}:
            raise HTTPException(409,'期报已变化，请刷新页面后重新确认删除')
        service.delete_period(c, pid)
    return {"ok": True}


@app.get("/api/deleted-periods")
def get_deleted_periods():
    with tx() as c:
        return service.deleted_periods(c)


@app.post("/api/deleted-periods/{tid}/restore")
def restore_deleted_period(tid:int):
    with tx() as c:
        return service.restore_period(c,tid)


@app.get("/api/periods/{pid}/report")
def get_report(pid: int):
    with tx() as c:
        return service.period_report(c, pid)


@app.put("/api/periods/{pid}/entries")
async def put_entries(pid: int, request: Request):
    b = await request.json()
    if b.get('patch') is not True:
        raise HTTPException(409,'页面已更新，请刷新后继续填写；已有数据不会被整行空值覆盖')
    with tx() as c:
        actual=service.get_period(c,pid)
        if b.get('period')!={key:actual[key] for key in ('year','month','day_range')}:
            raise HTTPException(409,'期报已变化，请刷新页面后继续填写')
        service.save_entries(c, pid, b["rows"],partial=True)
        return service.period_report(c, pid)


@app.get("/api/periods/{pid}/export")
def export(pid: int):
    with tx() as c:
        data, name = excel_export.export_period(c, pid)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


# ---------- 每日投食 / 伤苗 ----------

@app.get("/api/daily")
def get_daily(year: int, month: int, kind: str):
    with tx() as c:
        return service.daily_grid(c, year, month, kind)


@app.put("/api/daily")
async def put_daily(request: Request):
    b = await request.json()
    with tx() as c:
        service.save_daily(c, int(b["year"]), int(b["month"]), b["kind"], b["cells"])
    return {"ok": True}


# ---------- 导入 Excel ----------

@app.post("/api/import/report")
async def import_report(file: UploadFile = File(...), preview_only: bool = Form(True), overwrite: bool = Form(False)):
    data=await file.read(10*1024*1024+1)
    if len(data)>10*1024*1024:
        raise HTTPException(400,"文件太大")
    try:
        plan=report_import.parse(data)
    except (TypeError,KeyError):
        raise ValueError("报表数据字段不完整，请使用网页导出的完整文件")
    with tx() as c:
        summary=report_import.preview(c,plan,overwrite)
        if preview_only:
            return summary
        return {**summary,**report_import.apply(c,plan,overwrite)}

@app.post("/api/import/feed")
async def import_feed(file: UploadFile = File(...), year: int = Form(...), month: int = Form(...), preview_only: bool = Form(True)):
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "文件太大")
    plan = feed_import.parse(data, year, month)
    with tx() as c:
        summary = feed_import.preview(c, plan)
        if preview_only:
            return summary
        return {**summary, **feed_import.apply(c, plan)}


@app.post("/api/import")
async def import_excel(file: UploadFile = File(...)):
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "文件太大")
    try:
        with tx() as c:
            return excel_io.import_workbook(c, data)
    except ValueError:
        raise
    except Exception:
        raise HTTPException(400, "无法读取这个文件，请确认是原报表格式的 .xlsx")
