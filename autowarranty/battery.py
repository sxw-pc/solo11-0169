from .store import connect,source,at,repeated

def pack(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'battery_pack',file)
        if repeated(db,'battery_pack',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['vin'],)).fetchone():raise ValueError('unknown_vehicle')
        if min(d['nominal_ah'],d['cell_count'])<=0:raise ValueError('invalid_pack')
        db.execute('INSERT INTO battery_pack VALUES(?,?,?,?,?)',(d['id'],d['vin'],d['nominal_ah'],d['cell_count'],sid))
        return {'id':d['id'],'replayed':False}
def charge(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'charge_test',file)
        if repeated(db,'charge_test',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM battery_pack WHERE id=?',(d['pack_id'],)).fetchone():raise ValueError('unknown_pack')
        if not 0<=d['start_soc']<d['end_soc']<=100 or d['amp_hours']<=0:raise ValueError('invalid_charge')
        db.execute('INSERT INTO charge_test VALUES(?,?,?,?,?,?,?,?,?)',(d['id'],d['pack_id'],d['start_soc'],d['end_soc'],d['amp_hours'],d['temp_c'],at(d['started_at']),at(d['ended_at']),sid))
        return {'id':d['id'],'replayed':False}
def resistance(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'resistance_test',file)
        if repeated(db,'resistance_test',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM battery_pack WHERE id=?',(d['pack_id'],)).fetchone():raise ValueError('unknown_pack')
        db.execute('INSERT INTO resistance_test VALUES(?,?,?,?,?,?)',(d['id'],d['pack_id'],d['milliohms'],d['temp_c'],at(d['measured_at']),sid))
        return {'id':d['id'],'replayed':False}
def tests(path):
    with connect(path) as db:return {'charge':[dict(x) for x in db.execute('SELECT * FROM charge_test ORDER BY id')],
                                    'resistance':[dict(x) for x in db.execute('SELECT * FROM resistance_test ORDER BY id')]}
