from .store import connect,source,at,repeated

def run(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'emission_run',file)
        if repeated(db,'emission_run',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['vin'],)).fetchone():raise ValueError('unknown_vehicle')
        if min(d['dyno_load_kw'],d['exhaust_lpm'],d['dilution_factor'])<=0:raise ValueError('invalid_run')
        db.execute('INSERT INTO emission_run VALUES(?,?,?,?,?,?,?,?,?)',(d['id'],d['vin'],d['phase'],at(d['started_at']),at(d['ended_at']),d['dyno_load_kw'],d['exhaust_lpm'],d['dilution_factor'],sid))
        return {'id':d['id'],'replayed':False}
def sample(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'gas_sample',file)
        if repeated(db,'gas_sample',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM emission_run WHERE id=?',(d['run_id'],)).fetchone():raise ValueError('unknown_run')
        if d['ppm']<0:raise ValueError('negative_ppm')
        db.execute('INSERT INTO gas_sample VALUES(?,?,?,?,?,?,?)',(d['id'],d['run_id'],d['compound'],d['ppm'],d['analyzer'],at(d['sampled_at']),sid))
        return {'id':d['id'],'replayed':False}
def readings(path):
    with connect(path) as db:return {'runs':[dict(x) for x in db.execute('SELECT * FROM emission_run ORDER BY id')],
                                    'samples':[dict(x) for x in db.execute('SELECT * FROM gas_sample ORDER BY id')]}
