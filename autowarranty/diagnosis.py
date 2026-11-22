from .store import connect,source,repeated,at,money

def vehicle(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'vehicle',file)
        old=db.execute('SELECT source_id FROM vehicle WHERE vin=?',(d['vin'],)).fetchone()
        if old:
            if old['source_id']!=sid:raise ValueError('vin_conflict')
            return {'vin':d['vin'],'replayed':True}
        if not d['vin'] or not d['owner']:raise ValueError('vehicle_identity_missing')
        db.execute('INSERT INTO vehicle VALUES(?,?,?,?,?)',(d['vin'],d['owner'],d['model'],'registered',sid))
        return {'vin':d['vin'],'status':'registered'}

def diagnose(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'diagnosis',file)
        if repeated(db,'diagnosis',d['id'],sid):return {'id':d['id'],'replayed':True}
        vehicle=db.execute('SELECT * FROM vehicle WHERE vin=?',(d['vin'],)).fetchone()
        if not vehicle:raise ValueError('unknown_vehicle')
        if d['device_vin']!=d['vin']:raise ValueError('diagnostic_vin_mismatch')
        if not d['work_code'] or not d['symptom']:raise ValueError('diagnosis_incomplete')
        db.execute('INSERT INTO diagnosis VALUES(?,?,?,?,?,?,?,?)',(d['id'],d['vin'],d['device_vin'],d['symptom'],d['work_code'],at(d['at']),'confirmed',sid))
        return {'id':d['id'],'status':'confirmed'}

def authorize(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'authorization',file)
        if repeated(db,'authorization',d['id'],sid):return {'id':d['id'],'replayed':True}
        diag=db.execute('SELECT * FROM diagnosis WHERE id=?',(d['diagnosis_id'],)).fetchone()
        if not diag:raise ValueError('unknown_diagnosis')
        vehicle=db.execute('SELECT * FROM vehicle WHERE vin=?',(diag['vin'],)).fetchone()
        if d['owner']!=vehicle['owner'] or d['work_code']!=diag['work_code']:raise ValueError('owner_or_scope_mismatch')
        valid=at(d['valid_to'])
        if valid<=diag['at']:raise ValueError('authorization_expired')
        db.execute('INSERT INTO authorization VALUES(?,?,?,?,?,?,?,?)',(d['id'],d['diagnosis_id'],d['owner'],d['work_code'],money(d['max_cost']),valid,'active',sid))
        return {'id':d['id'],'status':'active'}
