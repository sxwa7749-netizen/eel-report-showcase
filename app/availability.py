"""业务端只读开关标记，按已验证账号判断，不信任请求里的厂名。"""
import os
from pathlib import Path
from starlette.responses import HTMLResponse,JSONResponse


def paused_system(role,farm,directory=None):
    directory=directory or os.environ.get('AVAILABILITY_DIR','')
    if not directory:return None
    root=Path(directory)
    if (root/'eel.closed').is_file():return '鳗场系统'
    if role=='boss':key,name='eel-boss','老板端'
    elif role=='admin' and farm in ('demo','demo2','demo3'):
        key='eel-'+farm;name={'demo':'示例管理端','demo2':'示例二管理端','demo3':'示例三管理端'}[farm]
    else:return None
    return name if (root/(key+'.closed')).is_file() else None


def paused_response(name,api=False):
    message=name+'已暂停访问，请稍后再试。'
    headers={'Cache-Control':'no-store','Retry-After':'300'}
    if api:return JSONResponse({'detail':message},status_code=503,headers=headers)
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>系统维护中</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f6fa;color:#26364c;font:16px system-ui}main{background:white;border:1px solid #e7ecf3;border-radius:16px;padding:36px;margin:24px;max-width:420px}h1{font-size:24px}p{color:#7a889c;line-height:1.8}a{color:#2878f7}</style><main><h1>'+message+'</h1><p>已保存的数据会保留。你可以稍后重试，或使用其他已开放账号登录。</p><a href="login">返回登录</a></main></html>'
    return HTMLResponse(page,status_code=503,headers=headers)
