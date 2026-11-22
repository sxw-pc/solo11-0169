from .store import connect,source,repeated,at,money

def part(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'part',file)
        if repeated(db,'part',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not d['supplier'] or not d['batch']:raise ValueError('part_trace_missing')
        recalled=1 if d.get('recalled') else 0
        db.execute('INSERT INTO part VALUES(?,?,?,?,?,?,?,?)',(d['id'],d['model'],d['work_code'],d['batch'],d['supplier'],recalled,'recalled' if recalled else 'ready',sid))
        return {'id':d['id'],'status':'recalled' if recalled else 'ready'}

def execute(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'repair',file)
        if repeated(db,'repair',d['id'],sid):return {'id':d['id'],'replayed':True}
        v=db.execute('SELECT * FROM vehicle WHERE vin=?',(d['vin'],)).fetchone()
        diag=db.execute('SELECT * FROM diagnosis WHERE id=?',(d['diagnosis_id'],)).fetchone()
        auth=db.execute('SELECT * FROM authorization WHERE id=?',(d['authorization_id'],)).fetchone()
        part=db.execute('SELECT * FROM part WHERE id=?',(d['part_id'],)).fetchone()
        if not v or not diag or not auth or not part:raise ValueError('repair_input_missing')
        moment=at(d['at']);cost=money(d['cost'])
        if diag['vin']!=v['vin'] or auth['diagnosis_id']!=diag['id'] or auth['work_code']!=d['work_code'] or diag['work_code']!=d['work_code']:
            raise ValueError('work_not_authorized')
        if auth['valid_to']<moment or cost>auth['max_cents']:raise ValueError('authorization_expired_or_over_budget')
        if part['status']!='ready' or part['model']!=v['model'] or part['work_code']!=d['work_code']:raise ValueError('part_incompatible_or_recalled')
        db.execute('INSERT INTO repair VALUES(?,?,?,?,?,?,?,?,?,?,?)',(d['id'],d['vin'],diag['id'],auth['id'],part['id'],d['work_code'],cost,d['technician'],moment,'performed',sid))
        db.execute("UPDATE vehicle SET status='repairing' WHERE vin=?",(d['vin'],))
        return {'id':d['id'],'status':'performed'}

def inspect(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'inspection',file)
        if repeated(db,'inspection',d['id'],sid):return {'id':d['id'],'replayed':True}
        repair=db.execute('SELECT * FROM repair WHERE id=?',(d['repair_id'],)).fetchone()
        if not repair:raise ValueError('unknown_repair')
        if d['inspector']==repair['technician']:raise ValueError('independent_inspector_required')
        ok=1 if d['road_test_ok'] else 0
        status='released' if ok else 'rework'
        db.execute('INSERT INTO inspection VALUES(?,?,?,?,?,?)',(d['id'],d['repair_id'],d['inspector'],ok,status,sid))
        db.execute('UPDATE repair SET status=? WHERE id=?',(status,repair['id']))
        db.execute('UPDATE vehicle SET status=? WHERE vin=?',('delivered' if ok else 'repairing',repair['vin']))
        return {'id':d['id'],'status':status}
