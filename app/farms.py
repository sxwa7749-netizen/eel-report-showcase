"""多个养殖厂：每个厂一个独立的数据库文件，共用同一套程序。

- 示例继续用原来的 data/demo.db，不迁移。
- 新厂在 .env 里设置了它的养殖录入密码才启用（没设密码的厂不出现）。
- 每次请求属于哪个厂，由登录中间件写进 current，tx() 据此打开对应数据库。
要加厂：在 FARMS 里加一行，并在 .env 里设置对应的密码变量。
"""
import os
from contextvars import ContextVar

FARMS = [
    # id 用在网址/数据库文件名里，不要改；name 是页面上显示的厂名，可以改
    {"id": "demo", "name": "示例", "mark": "一", "db": "demo.db", "password_env": "APP_PASSWORD"},
    {"id": "demo2", "name": "示例二", "mark": "二", "db": "demo2.db", "password_env": "DEMO2_PASSWORD"},
    {"id": "demo3", "name": "示例三", "mark": "三", "db": "demo3.db", "password_env": "DEMO3_PASSWORD"},
]
DEFAULT = "demo"
BY_ID = {f["id"]: f for f in FARMS}

current: ContextVar[str] = ContextVar("farm", default=DEFAULT)


def password(fid):
    return os.environ.get(BY_ID[fid]["password_env"], "")


def enabled():
    """启用的厂：示例一直启用；其他厂设置了密码才启用。
    本地试用（不设任何密码）时，FARMS_ALL=1 可以打开全部厂。"""
    if os.environ.get("FARMS_ALL") == "1":
        return list(FARMS)
    return [f for f in FARMS if f["id"] == DEFAULT or password(f["id"])]


def enabled_ids():
    return [f["id"] for f in enabled()]


def get(fid=None):
    return BY_ID[fid or current.get()]


def title(fid=None):
    """「示例鳗场」这样的厂名，用在页面和导出的 Excel 里"""
    return f"{get(fid)['name']}鳗场"


def public(fid=None):
    f = get(fid)
    return {"id": f["id"], "name": f["name"], "mark": f["mark"], "title": title(f["id"])}
