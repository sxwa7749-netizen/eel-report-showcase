"""老板工资台账；金额以分存储。"""
from decimal import Decimal, InvalidOperation


def period(year, month):
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise ValueError('工资年月不正确')


def money(value, label):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 10000000 or amount != amount.quantize(Decimal('0.01')):
            raise ValueError()
        return int(amount * 100)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'{label}应为非负金额，最多两位小数')


def listing(c, year, month):
    period(year, month)
    rows = []
    for r in c.execute('SELECT * FROM payroll WHERE year=? AND month=? AND NOT EXISTS(SELECT 1 FROM wage_legacy_promotions m WHERE m.payroll_id=payroll.id) ORDER BY id', (year, month)):
        row = dict(r)
        row['net_cents'] = row['salary_cents'] - row['deduction_cents'] + row['bonus_cents']
        rows.append(row)
    return dict(year=year, month=month, rows=rows, total_cents=sum(r['net_cents'] for r in rows),
                paid_cents=sum(r['net_cents'] for r in rows if r['paid']))


def save(c, body):
    try:
        year, month = int(body['year']), int(body['month'])
    except (KeyError, ValueError, TypeError):
        raise ValueError('请选择工资月份')
    period(year, month)
    name = str(body.get('name') or '').strip()
    if not name or len(name) > 60:
        raise ValueError('请填写员工姓名（最多60字）')
    salary = money(body.get('salary', ''), '固定月薪')
    deduction = money(body.get('deduction', 0), '请假扣款')
    bonus = money(body.get('bonus', 0), '奖金')
    if deduction > salary + bonus:
        raise ValueError('请假扣款不能超过月薪与奖金合计')
    try:
        days = Decimal(str(body.get('leave_days', 0)))
        if not days.is_finite() or not 0 <= days <= 31 or days != days.quantize(Decimal('0.1')):
            raise ValueError()
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('请假天数应为0至31，最多一位小数')
    note = str(body.get('note') or '').strip()
    if len(note) > 1000:
        raise ValueError('备注最多1000字')
    paid = body.get('paid', False)
    if not isinstance(paid, bool):
        raise ValueError('发放状态不正确')
    rid = body.get('id')
    if rid and c.execute('SELECT 1 FROM wage_legacy_promotions WHERE payroll_id=?', (rid,)).fetchone():
        raise ValueError('这条记录已切换到自动台账，原手工记录仅作历史保留')
    month_key = f'{year:04d}-{month:02d}'
    profile = c.execute('SELECT id FROM wage_employees WHERE name=? AND start_month<=? AND (end_month IS NULL OR end_month>=?)', (name, month_key, month_key)).fetchone()
    if profile:
        linked = c.execute('SELECT 1 FROM wage_legacy_links WHERE payroll_id=? AND employee_id=?', (rid or 0, profile['id'])).fetchone()
        if not linked:
            raise ValueError('这名员工已采用自动工资台账，请在日常记录或员工档案中调整，避免重复计薪')
    if rid and not c.execute('SELECT 1 FROM payroll WHERE id=? AND year=? AND month=?', (rid, year, month)).fetchone():
        raise LookupError('工资记录不存在')
    duplicate = c.execute('SELECT id FROM payroll WHERE year=? AND month=? AND name=?', (year, month, name)).fetchone()
    if duplicate and duplicate['id'] != rid:
        raise ValueError('这个月已存在同名员工，请使用姓名加编号区分')
    values = (name, salary, deduction, bonus, str(days), int(paid), note)
    if rid:
        c.execute('UPDATE payroll SET name=?,salary_cents=?,deduction_cents=?,bonus_cents=?,leave_days=?,paid=?,note=? WHERE id=?', (*values, rid))
    else:
        c.execute('INSERT INTO payroll(name,salary_cents,deduction_cents,bonus_cents,leave_days,paid,note,year,month) VALUES(?,?,?,?,?,?,?,?,?)', (*values, year, month))
    return listing(c, year, month)
