from .store import connect,source,at,repeated

def record(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'title_record',file)
        if repeated(db,'title_record',d['id'],sid):return {'id':d['id'],'replayed':True}
        db.execute('INSERT INTO title_record VALUES(?,?,?,?,?,?,?,?)',(d['id'],d['jurisdiction'],d['vin'],d['owner'],d['title_no'],at(d['registered_at']),d['status'],sid))
        return {'id':d['id'],'replayed':False}
def lien(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'lien_record',file)
        if repeated(db,'lien_record',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM title_record WHERE id=?',(d['title_id'],)).fetchone():raise ValueError('unknown_title')
        db.execute('INSERT INTO lien_record VALUES(?,?,?,?,?,?,?)',(d['id'],d['title_id'],d['creditor'],d['amount_cents'],at(d['recorded_at']),at(d['released_at']) if d.get('released_at') else None,sid))
        return {'id':d['id'],'replayed':False}
def scan(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'frame_scan',file)
        if repeated(db,'frame_scan',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['vin'],)).fetchone():raise ValueError('unknown_vehicle')
        db.execute('INSERT INTO frame_scan VALUES(?,?,?,?,?,?)',(d['id'],d['vin'],d['frame_serial'],d['engine_serial'],at(d['scanned_at']),sid))
        return {'id':d['id'],'replayed':False}
def records(path):
    with connect(path) as db:return {'titles':[dict(x) for x in db.execute('SELECT * FROM title_record ORDER BY id')],
                                    'liens':[dict(x) for x in db.execute('SELECT * FROM lien_record ORDER BY id')],
                                    'frames':[dict(x) for x in db.execute('SELECT * FROM frame_scan ORDER BY id')]}
