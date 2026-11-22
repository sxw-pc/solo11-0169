from .store import connect,source,at,repeated

def asset(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'loaner',file)
        if repeated(db,'loaner',d['id'],sid):return {'id':d['id'],'replayed':True}
        db.execute('INSERT INTO loaner VALUES(?,?,?,?)',(d['id'],d['plate'],d['model'],sid))
        return {'id':d['id'],'replayed':False}
def agreement(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'loan_agreement',file)
        if repeated(db,'loan_agreement',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['customer_vin'],)).fetchone():raise ValueError('unknown_customer_vehicle')
        if not db.execute('SELECT 1 FROM loaner WHERE id=?',(d['loaner_id'],)).fetchone():raise ValueError('unknown_loaner')
        db.execute('INSERT INTO loan_agreement VALUES(?,?,?,?,?,?,?,?)',(d['id'],d['customer_vin'],d['loaner_id'],d['borrower'],at(d['started_at']),at(d['due_at']),d['plan'],sid))
        return {'id':d['id'],'replayed':False}
def fix(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'trip_fix',file)
        if repeated(db,'trip_fix',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM loan_agreement WHERE id=?',(d['agreement_id'],)).fetchone():raise ValueError('unknown_agreement')
        db.execute('INSERT INTO trip_fix VALUES(?,?,?,?,?,?,?)',(d['id'],d['agreement_id'],at(d['observed_at']),d['x_m'],d['y_m'],d['accuracy_m'],sid))
        return {'id':d['id'],'replayed':False}
def toll(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'toll_receipt',file)
        if repeated(db,'toll_receipt',d['id'],sid):return {'id':d['id'],'replayed':True}
        db.execute('INSERT INTO toll_receipt VALUES(?,?,?,?,?,?)',(d['id'],d['plate'],d['gate'],at(d['passed_at']),d['cents'],sid))
        return {'id':d['id'],'replayed':False}
def trips(path):
    with connect(path) as db:return {'fixes':[dict(x) for x in db.execute('SELECT * FROM trip_fix ORDER BY id')],
                                    'tolls':[dict(x) for x in db.execute('SELECT * FROM toll_receipt ORDER BY id')]}
