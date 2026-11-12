"""网页报表往返导入：新版原始备份页，以及旧版只有打印页的导出文件。"""
import calendar
from io import BytesIO
import re

from openpyxl import load_workbook

from . import farms, security, service,feed_import
from .calc import CATEGORIES, INPUT_FIELDS

BACKUP_SHEET = '系统数据'
DAILY_SHEET = '系统每日记录'
FIELDS = ['name', 'category', *INPUT_FIELDS, 'note']


def number(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or (isinstance(value, str) and value.startswith('=')):
        raise ValueError('报表导入需要实际数字，不能使用公式')
    return service.num(value)


def parse(data):
    security.validate_xlsx(data)
    try:
        wb = load_workbook(BytesIO(data), data_only=False)
    except Exception:
        raise ValueError('无法读取报表，请选择网页导出的.xlsx文件')
    if feed_import.is_feed_workbook(wb):
        raise feed_import.FeedWorkbookDetected()
    security.check_workbook(wb)
    rows, daily, warnings = [], [], []
    if BACKUP_SHEET in wb.sheetnames:
        ws = wb[BACKUP_SHEET]
        if ws['A1'].value != 'EEL-REPORT-BACKUP' or ws['B1'].value != 1:
            raise ValueError('暂不支持这份报表的数据格式版本')
        fid = str(ws['B2'].value)
        year, month, day_range = int(ws['B3'].value), int(ws['B4'].value), str(ws['B5'].value)
        if fid != farms.current.get():
            raise ValueError('文件所属厂与当前登录的厂不一致，请切换到对应厂的录入账号')
        if [ws.cell(8, col).value for col in range(1,len(FIELDS)+1)] != FIELDS:
            raise ValueError('报表的系统数据表头已改变，无法可靠导入')
        for values in ws.iter_rows(min_row=9,max_col=len(FIELDS),values_only=True):
            if values[0] is None:
                continue
            row=dict(zip(FIELDS,values))
            for field in INPUT_FIELDS:
                row[field]=number(row[field])
            rows.append(row)
        if DAILY_SHEET in wb.sheetnames:
            for day,shift,name,kind,value in wb[DAILY_SHEET].iter_rows(min_row=2,max_col=5,values_only=True):
                if name is not None:
                    daily.append(dict(day=int(day),shift=shift,name=str(name),kind=kind,value=number(value)))
        source='新版网页报表'
    elif '报表竖版' in wb.sheetnames:
        ws=wb['报表竖版']
        match=re.fullmatch(r'(.+鳗场)报表\s+(\d{4})年(\d{1,2})月[（(]([^）)]+)[）)]',str(ws['A1'].value or '').strip())
        if not match:
            raise ValueError('无法识别旧报表的厂名或年月，请使用网页导出的完整文件')
        title,y,m,day_range=match.groups()
        if title != farms.title():
            raise ValueError(f'这份报表属于{title}，请登录该厂的录入账号后导入')
        year,month=int(y),int(m)
        for values in ws.iter_rows(min_row=3,max_col=12,values_only=True):
            name,cat,feed,conv,gain,orig,weight,injury,count,p,daily_feed,points=values
            if name is None or cat not in CATEGORIES:
                continue
            injury=number(injury);count=number(count)
            rows.append(dict(name=str(name),category=cat,feed_kg=number(feed),conv_rate=number(conv),
                             orig_weight=number(orig),injured=injury,orig_count=count+(injury or 0) if count is not None else None,
                             daily_feed=number(daily_feed),cur_count_override=None,note=None))
        for title,kind in (('投喂月表','feed'),('伤苗月表','injury')):
            if title not in wb.sheetnames:
                continue
            mapping={};day=None
            for line in wb[title].iter_rows(values_only=True):
                if len(line)>1 and line[0]=='日期' and line[1]=='班次':
                    mapping={i:str(v) for i,v in enumerate(line) if i>=2 and v is not None and str(v).endswith('#')}
                    day=None
                    continue
                if len(line)<2 or line[1] not in service.SHIFTS:
                    continue
                if isinstance(line[0],(int,float)):
                    day=int(line[0])
                if day is None:
                    continue
                for col,name in mapping.items():
                    value=number(line[col])
                    if value is not None:
                        daily.append(dict(day=day,shift=line[1],name=name,kind=kind,value=value))
        source='旧版网页报表'
        warnings.append('旧版文件按导出时的数值恢复，原来的手填覆盖和自动沿用关系无法完整还原。')
    else:
        raise ValueError('这不是网页导出的报表；饲料表请使用每日饲料导入，原模板请使用原Excel导入')
    # 先校验年月，再校验每一天，避免不存在的日期混入。
    if not 2000<=year<=2100 or not 1<=month<=12:
        raise ValueError('报表年月不正确')
    if day_range!='全月':
        match=re.fullmatch(r'(\d{1,2})-(\d{1,2})',day_range)
        if not match or not 1<=int(match[1])<=int(match[2])<=31:
            raise ValueError('报表日期范围不正确')
    seen=set()
    for row in rows:
        row['name']=str(row['name']).strip()
        if not row['name'] or len(row['name'])>100 or row['name'] in seen or row['category'] not in CATEGORIES:
            raise ValueError('报表有空池号、重复池号或错误分类')
        seen.add(row['name'])
        row['note']=str(row.get('note') or '') or None
    if not rows:
        raise ValueError('报表里没有可恢复的池号数据')
    if len(rows)>1000:
        raise ValueError('池号数量超出导入上限')
    for cell in daily:
        if not 1<=cell['day']<=calendar.monthrange(year,month)[1] or cell['shift'] not in service.SHIFTS or cell['kind'] not in service.DAILY_KINDS or cell['value'] is None:
            raise ValueError('每日记录中存在无效日期、班次或数字')
    return dict(year=year,month=month,day_range=day_range,rows=rows,daily=daily,source=source,warnings=warnings)


def preview(c,plan,overwrite=False):
    period=c.execute('SELECT id FROM periods WHERE year=? AND month=? AND day_range=?',(plan['year'],plan['month'],plan['day_range'])).fetchone()
    existing=0
    if period:
        existing=c.execute('SELECT COUNT(*) FROM entries WHERE period_id=? AND (orig_weight IS NOT NULL OR orig_count IS NOT NULL OR feed_kg IS NOT NULL)',(period[0],)).fetchone()[0]
    return dict(label=f"{plan['year']}年{plan['month']}月（{plan['day_range']}）",farm=farms.title(),source=plan['source'],
                ponds=len(plan['rows']),daily_cells=len(plan['daily']),existing_ponds=existing,period_exists=bool(period),
                mode='用文件非空数据更新' if overwrite else '只补空白，保留已有非空值',warnings=plan['warnings'])


def apply(c,plan,overwrite=False):
    existing={p['name']:p for p in service.list_ponds(c)}
    ids={}
    for row in plan['rows']:
        name=row['name']
        if name in existing:
            ids[name]=existing[name]['id']
        else:
            ids[name]=c.execute('INSERT INTO ponds(name,category,sort) VALUES(?,?,?)',(name,row['category'],len(existing)+len(ids)+1)).lastrowid
    for cell in plan['daily']:
        name=cell['name']
        if name not in ids:
            ids[name]=existing[name]['id'] if name in existing else c.execute("INSERT INTO ponds(name,category,sort) VALUES(?,'新',?)",(name,len(existing)+len(ids)+1)).lastrowid
    period=c.execute('SELECT id FROM periods WHERE year=? AND month=? AND day_range=?',(plan['year'],plan['month'],plan['day_range'])).fetchone()
    pid=period[0] if period else c.execute('INSERT INTO periods(year,month,day_range,carry_live) VALUES(?,?,?,0)',(plan['year'],plan['month'],plan['day_range'])).lastrowid
    for row in plan['rows']:
        pond=ids[row['name']]
        c.execute('INSERT OR IGNORE INTO entries(period_id,pond_id,category) VALUES(?,?,?)',(pid,pond,row['category']))
        old=c.execute('SELECT * FROM entries WHERE period_id=? AND pond_id=?',(pid,pond)).fetchone()
        pristine=not any(old[f] is not None for f in INPUT_FIELDS)
        if overwrite or pristine or not old['category']:
            c.execute('UPDATE entries SET category=? WHERE period_id=? AND pond_id=?',(row['category'],pid,pond))
        for field in [*INPUT_FIELDS,'note']:
            value=row.get(field)
            if value is not None and value!='' and (overwrite or old[field] is None or old[field]==''):
                c.execute(f'UPDATE entries SET {field}=? WHERE period_id=? AND pond_id=?',(value,pid,pond))
    for cell in plan['daily']:
        old=c.execute('SELECT 1 FROM daily WHERE year=? AND month=? AND day=? AND shift=? AND pond_id=? AND kind=?',(plan['year'],plan['month'],cell['day'],cell['shift'],ids[cell['name']],cell['kind'])).fetchone()
        if overwrite or not old:
            service.save_daily(c,plan['year'],plan['month'],cell['kind'],[dict(day=cell['day'],shift=cell['shift'],pond_id=ids[cell['name']],value=cell['value'])])
    return {'period_id':pid,'replaced':bool(period),'mode':'merge'}
