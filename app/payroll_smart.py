"""员工工资档案、日常流水、按自然月计算及发放快照。"""
import calendar
import datetime
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from . import payroll


def ym(year, month):
    payroll.period(year, month)
    return f'{year:04d}-{month:02d}'


def month_value(value):
    try:
        y, m = map(int, str(value).split('-'))
        if str(value) != ym(y, m):
            raise ValueError()
        return str(value)
    except (ValueError, TypeError):
        raise ValueError('请选择正确的生效月份')


def employee(c, eid):
    row = c.execute('SELECT * FROM wage_employees WHERE id=?', (eid,)).fetchone()
    if not row:
        raise LookupError('员工档案不存在')
    return dict(row)


def active(e, month):
    return e['start_month'] <= month and (not e['end_month'] or month <= e['end_month'])


def rate(c, eid, month):
    row = c.execute('SELECT salary_cents FROM wage_rates WHERE employee_id=? AND effective_month<=? ORDER BY effective_month DESC LIMIT 1', (eid, month)).fetchone()
    return row[0] if row else None


def seed_legacy(c):
    """只从旧台账初始化档案；旧工资及已发放金额不转换、不重算。"""
    if c.execute("SELECT 1 FROM wage_metadata WHERE key='legacy_seeded'").fetchone():
        return
    for r in c.execute('SELECT * FROM payroll ORDER BY year,month,id').fetchall():
        month = ym(r['year'], r['month'])
        c.execute('INSERT OR IGNORE INTO wage_employees(name,start_month) VALUES(?,?)', (r['name'], month))
        eid = c.execute('SELECT id FROM wage_employees WHERE name=?', (r['name'],)).fetchone()[0]
        c.execute('INSERT OR IGNORE INTO wage_rates VALUES(?,?,?)', (eid, month, r['salary_cents']))
        c.execute('INSERT OR IGNORE INTO wage_legacy_links VALUES(?,?)', (r['id'], eid))
    c.execute("INSERT INTO wage_metadata VALUES('legacy_seeded','1')")


def locked(c, eid, year, month):
    return c.execute('SELECT * FROM wage_settlements WHERE employee_id=? AND year=? AND month=? AND revoked_at IS NULL', (eid, year, month)).fetchone()


def check_edit(c, eid, year, month):
    if locked(c, eid, year, month):
        raise ValueError('本月已结算，请先撤销结算再修改')
    e = employee(c, eid)
    if c.execute('SELECT 1 FROM payroll p LEFT JOIN wage_legacy_links l ON l.payroll_id=p.id WHERE (p.name=? OR l.employee_id=?) AND p.year=? AND p.month=? AND NOT EXISTS(SELECT 1 FROM wage_legacy_promotions m WHERE m.payroll_id=p.id)', (e['name'], eid, year, month)).fetchone():
        raise ValueError('这个月已有历史手工工资，请在历史记录中调整，避免重复计薪')


def has_history(c, eid):
    """员工是否已有任何工资相关记录。有记录的不能删除，只能设为离职。"""
    e = employee(c, eid)
    checks = (
        ('SELECT 1 FROM wage_events WHERE employee_id=? LIMIT 1', (eid,)),
        ('SELECT 1 FROM wage_settlements WHERE employee_id=? LIMIT 1', (eid,)),
        ('SELECT 1 FROM wage_overrides WHERE employee_id=? LIMIT 1', (eid,)),
        ('SELECT 1 FROM wage_legacy_links WHERE employee_id=? LIMIT 1', (eid,)),
        ('SELECT 1 FROM wage_legacy_promotions WHERE employee_id=? LIMIT 1', (eid,)),
        ('SELECT 1 FROM payroll WHERE name=? LIMIT 1', (e['name'],)),
    )
    return any(c.execute(sql, args).fetchone() for sql, args in checks)


def delete_employee(c, eid):
    employee(c, eid)
    if has_history(c, eid):
        raise ValueError('这名员工已有工资记录，不能删除（删除会让以前的账对不上）。如果已经离职，请点「设为离职」，以后就不再生成工资。')
    c.execute('DELETE FROM wage_rates WHERE employee_id=?', (eid,))
    c.execute('DELETE FROM wage_employees WHERE id=?', (eid,))
    return {'ok': True}


def employees(c, year, month):
    month_key = ym(year, month)
    result = []
    for r in c.execute('SELECT * FROM wage_employees ORDER BY id'):
        e = dict(r)
        e['salary_cents'] = rate(c, e['id'], month_key)
        latest = c.execute('SELECT salary_cents FROM wage_rates WHERE employee_id=? ORDER BY effective_month DESC LIMIT 1', (e['id'],)).fetchone()
        e['default_salary_cents'] = latest[0] if latest else 0
        e['active'] = active(e, month_key)
        e['left'] = bool(e['end_month'] and e['end_month'] < month_key)
        e['deletable'] = not has_history(c, e['id'])
        result.append(e)
    return result


def save_employee(c, b):
    effective = month_value(b.get('effective_month'))
    name = str(b.get('name') or '').strip()
    if not name or len(name) > 60:
        raise ValueError('请填写员工姓名，最多60字')
    note = str(b.get('note') or '').strip()
    if len(note) > 1000:
        raise ValueError('备注最多1000字')
    salary = payroll.money(b.get('salary'), '固定月薪')
    eid = b.get('id')
    old = employee(c, eid) if eid else None
    if c.execute('SELECT 1 FROM wage_employees WHERE name=? AND id<>?', (name, eid or 0)).fetchone():
        raise ValueError('员工姓名重复，请在姓名后增加编号区分')
    end = month_value(b['end_month']) if b.get('end_month') else None
    start = old['start_month'] if old else effective
    if effective < start or (end and end < start):
        raise ValueError('月份不能早于员工开始计薪月份')
    if old:
        if end and c.execute('SELECT 1 FROM wage_events WHERE employee_id=? AND substr(date,1,7)>?', (eid, end)).fetchone():
            raise ValueError('停用月份之后已有日常记录，请先核对并调整记录')
        # 已结算月份采用锁定快照；不允许直接回改对应的月薪标准。
        next_rate = c.execute('SELECT MIN(effective_month) FROM wage_rates WHERE employee_id=? AND effective_month>?', (eid, effective)).fetchone()[0]
        for settled in c.execute('SELECT year,month FROM wage_settlements WHERE employee_id=? AND revoked_at IS NULL', (eid,)):
            key = ym(settled['year'], settled['month'])
            if effective <= key and (next_rate is None or key < next_rate) and rate(c, eid, key) != salary:
                raise ValueError('工资生效月份涉及已结算记录，请选择后续月份或先撤销该月结算')
        c.execute('UPDATE wage_employees SET name=?,end_month=?,note=? WHERE id=?', (name, end, note, eid))
    else:
        eid = c.execute('INSERT INTO wage_employees(name,start_month,end_month,note) VALUES(?,?,?,?)', (name, start, end, note)).lastrowid
    c.execute('INSERT INTO wage_rates VALUES(?,?,?) ON CONFLICT(employee_id,effective_month) DO UPDATE SET salary_cents=excluded.salary_cents', (eid, effective, salary))
    return {'id': eid}


def events(c, year, month):
    key = ym(year, month)
    return [dict(r) for r in c.execute('SELECT v.*,e.name FROM wage_events v JOIN wage_employees e ON e.id=v.employee_id WHERE substr(v.date,1,7)=? ORDER BY v.date DESC,v.id DESC', (key,))]


def save_event(c, b):
    try:
        date = datetime.date.fromisoformat(str(b.get('date')))
        if date.isoformat() != b.get('date'):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('请选择正确的记录日期')
    key = ym(date.year, date.month)
    if b.get('end_date') and b['end_date'] != date.isoformat() and not b.get('id'):
        try:
            end = datetime.date.fromisoformat(str(b['end_date']))
        except ValueError:
            raise ValueError('结束日期不正确')
        if b.get('kind') != 'leave' or not 0 <= (end-date).days < 366:
            raise ValueError('请假日期范围不正确，最长支持一年')
        ids = []
        for offset in range((end-date).days+1):
            item = dict(b, date=(date+datetime.timedelta(days=offset)).isoformat())
            item.pop('end_date')
            ids.append(save_event(c, item)['id'])
        return {'ids': ids}
    eid = b.get('employee_id')
    e = employee(c, eid)
    if not active(e, key):
        raise ValueError('该日期不在员工计薪期间内')
    check_edit(c, eid, date.year, date.month)
    rid = b.get('id')
    if rid:
        old = c.execute('SELECT * FROM wage_events WHERE id=?', (rid,)).fetchone()
        if not old:
            raise LookupError('记录不存在')
        previous = datetime.date.fromisoformat(old['date'])
        check_edit(c, old['employee_id'], previous.year, previous.month)
    kind = b.get('kind')
    if kind not in ('leave', 'bonus', 'advance', 'adjust'):
        raise ValueError('记录类型不正确')
    days = Decimal(0)
    amount = 0
    if kind == 'adjust':
        # 其他加减：全勤奖、罚款、补发等；正数加、负数减，必须写原因
        amount = payroll.money(str(b.get('amount') or '').lstrip('-'), '金额')
        if amount <= 0:
            raise ValueError('加减金额必须大于0')
        if b.get('sign') == '-' or str(b.get('amount') or '').startswith('-'):
            amount = -amount
        if not str(b.get('note') or '').strip():
            raise ValueError('其他加减请写明原因，方便以后核对')
    elif kind == 'leave':
        try:
            days = Decimal(str(b.get('leave_days')))
            if not days.is_finite() or not 0 < days <= 1 or days != days.quantize(Decimal('0.1')):
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise ValueError('单日请假填写0.1至1天；多天请假请按日期分别记录')
        existing = sum((Decimal(r[0]) for r in c.execute("SELECT leave_days FROM wage_events WHERE employee_id=? AND date=? AND kind='leave' AND id<>?", (eid, date.isoformat(), rid or 0))), Decimal(0))
        if existing + days > 1:
            raise ValueError('同一员工同一天的请假合计不能超过1天')
    else:
        amount = payroll.money(b.get('amount'), '金额')
        if amount <= 0:
            raise ValueError('奖金或借支金额必须大于0')
    note = str(b.get('note') or '').strip()
    if len(note) > 1000:
        raise ValueError('备注最多1000字')
    values = (eid, date.isoformat(), kind, amount, str(days), note)
    if rid:
        c.execute('UPDATE wage_events SET employee_id=?,date=?,kind=?,amount_cents=?,leave_days=?,note=? WHERE id=?', (*values, rid))
    else:
        rid = c.execute('INSERT INTO wage_events(employee_id,date,kind,amount_cents,leave_days,note) VALUES(?,?,?,?,?,?)', values).lastrowid
    return {'id': rid}


def delete_event(c, rid):
    row = c.execute('SELECT * FROM wage_events WHERE id=?', (rid,)).fetchone()
    if not row:
        raise LookupError('记录不存在')
    date = datetime.date.fromisoformat(row['date'])
    check_edit(c, row['employee_id'], date.year, date.month)
    c.execute('DELETE FROM wage_events WHERE id=?', (rid,))


def overview(c, year, month):
    key = ym(year, month)
    days_in_month = calendar.monthrange(year, month)[1]
    staff = employees(c, year, month)
    records = events(c, year, month)
    legacy = payroll.listing(c, year, month)
    legacy_names = {r['name'] for r in legacy['rows']}
    legacy_ids = {r[0] for r in c.execute('SELECT l.employee_id FROM wage_legacy_links l JOIN payroll p ON p.id=l.payroll_id WHERE p.year=? AND p.month=? AND NOT EXISTS(SELECT 1 FROM wage_legacy_promotions m WHERE m.payroll_id=p.id)', (year, month))}
    for old in legacy['rows']:
        linked = c.execute('SELECT employee_id FROM wage_legacy_links WHERE payroll_id=?', (old['id'],)).fetchone()
        eid = linked[0] if linked else next((e['id'] for e in staff if e['name'] == old['name']), None)
        old['can_upgrade'] = bool(eid and not old['paid'] and old['deduction_cents'] == 0 and old['bonus_cents'] == 0
                                  and Decimal(old['leave_days']) == 0 and rate(c, eid, key) == old['salary_cents']
                                  and any(e['id'] == eid and e['active'] for e in staff)
                                  and not locked(c, eid, year, month))
    snapshots = {r['employee_id']: dict(r) for r in c.execute('SELECT * FROM wage_settlements WHERE year=? AND month=? AND revoked_at IS NULL', (year, month))}
    overrides = {r['employee_id']: dict(r) for r in c.execute('SELECT * FROM wage_overrides WHERE year=? AND month=?', (year, month))}
    rows = []
    for e in staff:
        eid = e['id']
        if eid in snapshots:
            saved = snapshots.pop(eid)
            row = json.loads(saved['detail_json'])
            row.update(paid=True, settlement_id=saved['id'], settled_at=saved['created_at'])
            rows.append(row)
            continue
        if not e['active'] or e['name'] in legacy_names or eid in legacy_ids:
            continue
        own = [r for r in records if r['employee_id'] == eid]
        auto_salary = e['salary_cents']
        ov = overrides.get(eid, {})
        if auto_salary is None and ov.get('salary_cents') is None:
            continue
        salary = ov['salary_cents'] if ov.get('salary_cents') is not None else auto_salary
        leave = sum((Decimal(r['leave_days']) for r in own if r['kind'] == 'leave'), Decimal(0))
        # 自动扣款按本月实际使用的月薪折算（手动改了月薪也随之折算）
        auto_deduction = int((Decimal(salary) * leave / days_in_month).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        deduction = ov['deduction_cents'] if ov.get('deduction_cents') is not None else auto_deduction
        bonus = sum(r['amount_cents'] for r in own if r['kind'] == 'bonus')
        advance = sum(r['amount_cents'] for r in own if r['kind'] == 'advance')
        adjust = sum(r['amount_cents'] for r in own if r['kind'] == 'adjust')
        rows.append(dict(employee_id=eid, name=e['name'], year=year, month=month, salary_cents=salary,
                         salary_auto_cents=auto_salary, salary_manual=ov.get('salary_cents') is not None,
                         leave_days=str(leave), month_days=days_in_month, deduction_cents=deduction,
                         deduction_auto_cents=auto_deduction, deduction_manual=ov.get('deduction_cents') is not None,
                         override_note=ov.get('note') or '', override_at=ov.get('updated_at'),
                         bonus_cents=bonus, advance_cents=advance, adjust_cents=adjust,
                         net_cents=salary-deduction+bonus-advance+adjust,
                         paid=False, events=own))
    return dict(year=year, month=month, month_days=days_in_month, employees=staff, events=records, rows=rows, legacy=legacy,
                total_cents=sum(r['net_cents'] for r in rows)+legacy['total_cents'],
                paid_cents=sum(r['net_cents'] for r in rows if r['paid'])+legacy['paid_cents'])


def save_override(c, b):
    """本月手动改数。salary / deduction 留空表示用自动计算；两项都留空即恢复自动。"""
    try:
        year, month, eid = int(b.get('year', 0)), int(b.get('month', 0)), int(b.get('employee_id'))
    except (TypeError, ValueError):
        raise ValueError('请选择员工和月份')
    key = ym(year, month)
    e = employee(c, eid)
    if not active(e, key):
        raise ValueError('该月份不在员工计薪期间内')
    check_edit(c, eid, year, month)

    def opt(name, label):
        v = b.get(name)
        return None if v is None or str(v).strip() == '' else payroll.money(v, label)

    salary = opt('salary', '本月月薪')
    deduction = opt('deduction', '请假扣款')
    note = str(b.get('note') or '').strip()
    if len(note) > 1000:
        raise ValueError('原因最多1000字')
    if salary is None and deduction is None:
        c.execute('DELETE FROM wage_overrides WHERE employee_id=? AND year=? AND month=?', (eid, year, month))
        return {'ok': True, 'cleared': True}
    if not note:
        raise ValueError('手动改数请写明原因，方便以后核对')
    if deduction is not None and salary is not None and deduction > salary:
        raise ValueError('请假扣款不能超过本月月薪')
    c.execute(
        'INSERT INTO wage_overrides(employee_id,year,month,salary_cents,deduction_cents,note,updated_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)'
        ' ON CONFLICT(employee_id,year,month) DO UPDATE SET salary_cents=excluded.salary_cents,'
        ' deduction_cents=excluded.deduction_cents, note=excluded.note, updated_at=CURRENT_TIMESTAMP',
        (eid, year, month, salary, deduction, note))
    return {'ok': True}


def settle(c, b):
    year, month, eid = int(b.get('year', 0)), int(b.get('month', 0)), b.get('employee_id')
    ym(year, month)
    if locked(c, eid, year, month):
        return {'ok': True}
    check_edit(c, eid, year, month)
    row = next((r for r in overview(c, year, month)['rows'] if r['employee_id'] == eid), None)
    if not row:
        raise ValueError('本月没有该员工的工资草稿')
    if row['net_cents'] < 0:
        raise ValueError('借支超过本月可发工资，请先核对记录，不能确认负数发放')
    if b.get('expected_net_cents') is not None and b['expected_net_cents'] != row['net_cents']:
        raise ValueError('工资明细刚刚发生变化，请刷新页面核对后再确认发放')
    c.execute('INSERT INTO wage_settlements(employee_id,year,month,detail_json) VALUES(?,?,?,?)', (eid, year, month, json.dumps(row, ensure_ascii=False)))
    return {'ok': True}


def settle_all(c, b):
    """一次确认多名员工已发放。expected：{员工id: 页面上看到的实发（分）}，
    任何一人金额对不上就整批不发（由调用方的事务回滚），避免按旧数字发放。"""
    year, month = int(b.get('year', 0)), int(b.get('month', 0))
    ym(year, month)
    expected = {int(k): int(v) for k, v in (b.get('expected') or {}).items()}
    if not expected:
        raise ValueError('请选择要发放的员工')
    rows = {r['employee_id']: r for r in overview(c, year, month)['rows']}
    count, total = 0, 0
    for eid, cents in expected.items():
        r = rows.get(eid)
        if not r:
            raise ValueError('有员工本月没有工资草稿，请刷新页面后再试')
        if r['paid']:
            continue
        if r['net_cents'] != cents:
            raise ValueError(f'{r["name"]}的工资刚刚有变化，请刷新页面核对后再发放')
        if r['net_cents'] < 0:
            raise ValueError(f'{r["name"]}的实发是负数（借支超过工资），请先核对')
        settle(c, dict(year=year, month=month, employee_id=eid, expected_net_cents=cents))
        count += 1
        total += cents
    return {'ok': True, 'count': count, 'total_cents': total}


def reopen(c, b):
    year, month = int(b.get('year', 0)), int(b.get('month', 0))
    ym(year, month)
    c.execute('UPDATE wage_settlements SET revoked_at=CURRENT_TIMESTAMP WHERE employee_id=? AND year=? AND month=? AND revoked_at IS NULL', (b.get('employee_id'), year, month))
    return {'ok': True}


def upgrade_legacy(c, rid):
    if c.execute('SELECT 1 FROM wage_legacy_promotions WHERE payroll_id=?', (rid,)).fetchone():
        return {'ok': True}
    row = c.execute('SELECT * FROM payroll WHERE id=?', (rid,)).fetchone()
    if not row:
        raise LookupError('原工资记录不存在')
    old = next((r for r in overview(c, row['year'], row['month'])['legacy']['rows'] if r['id'] == rid), None)
    if not old or not old['can_upgrade']:
        raise ValueError('仅支持未发放且无请假、扣款、奖金的旧记录切换；其他旧记录请保留原结算方式')
    linked = c.execute('SELECT employee_id FROM wage_legacy_links WHERE payroll_id=?', (rid,)).fetchone()
    eid = linked[0] if linked else c.execute('SELECT id FROM wage_employees WHERE name=?', (row['name'],)).fetchone()[0]
    c.execute('INSERT INTO wage_legacy_promotions(payroll_id,employee_id) VALUES(?,?)', (rid, eid))
    return {'ok': True}
