# 本地启动：浏览器打开 http://127.0.0.1:8100
# 想测试登录，可以先设置：$env:APP_PASSWORD="管理员密码"; $env:STAFF_PASSWORD="员工密码"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    python -m venv .venv
    .\.venv\Scripts\python -m pip install -r requirements.txt
}
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8100
