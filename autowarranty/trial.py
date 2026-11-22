from .store import connect,source,at,repeated

def assignment(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'trial_assignment',file)
        if repeated(db,'trial_assignment',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['vin'],)).fetchone():raise ValueError('unknown_vehicle')
        if d['arm'] not in ('new','control'):raise ValueError('invalid_arm')
        db.execute('INSERT INTO trial_assignment VALUES(?,?,?,?,?,?)',(d['id'],d['vin'],d['arm'],d['package_id'],at(d['assigned_at']),sid))
        return {'id':d['id'],'replayed':False}
def session(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'drive_session',file)
        if repeated(db,'drive_session',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['vin'],)).fetchone():raise ValueError('unknown_vehicle')
        db.execute('INSERT INTO drive_session VALUES(?,?,?,?,?,?,?)',(d['id'],d['vin'],at(d['started_at']),at(d['ended_at']),d['distance_km'],d['route_class'],sid))
        return {'id':d['id'],'replayed':False}
def alert(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'safety_alert',file)
        if repeated(db,'safety_alert',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM drive_session WHERE id=?',(d['session_id'],)).fetchone():raise ValueError('unknown_session')
        db.execute('INSERT INTO safety_alert VALUES(?,?,?,?,?,?)',(d['id'],d['session_id'],d['kind'],at(d['occurred_at']),d['severity'],sid))
        return {'id':d['id'],'replayed':False}
def captures(path):
    with connect(path) as db:return {t:[dict(x) for x in db.execute(f'SELECT * FROM {t} ORDER BY id')] for t in ('trial_assignment','drive_session','safety_alert')}
