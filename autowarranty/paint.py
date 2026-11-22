import json
from .store import connect,source,at,repeated

def sample(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'paint_sample',file)
        if repeated(db,'paint_sample',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['vin'],)).fetchone():raise ValueError('unknown_vehicle')
        db.execute('INSERT INTO paint_sample VALUES(?,?,?,?,?,?,?,?)',(d['id'],d['vin'],d['panel'],d['illuminant'],json.dumps(d['rgb']),json.dumps(d['spectrum']),at(d['measured_at']),sid))
        return {'id':d['id'],'replayed':False}
def pigment(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'pigment_lot',file)
        if repeated(db,'pigment_lot',d['id'],sid):return {'id':d['id'],'replayed':True}
        db.execute('INSERT INTO pigment_lot VALUES(?,?,?,?,?,?)',(d['id'],d['pigment'],d['batch'],json.dumps(d['spectrum']),d['available_ml'],sid))
        return {'id':d['id'],'replayed':False}
def panel(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'spray_panel',file)
        if repeated(db,'spray_panel',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['vin'],)).fetchone():raise ValueError('unknown_vehicle')
        db.execute('INSERT INTO spray_panel VALUES(?,?,?,?,?,?,?)',(d['id'],d['vin'],json.dumps(d['recipe']),d['illuminant'],json.dumps(d['rgb']),at(d['sprayed_at']),sid))
        return {'id':d['id'],'replayed':False}
def captures(path):
    with connect(path) as db:return {t:[dict(x) for x in db.execute(f'SELECT * FROM {t} ORDER BY id')] for t in ('paint_sample','pigment_lot','spray_panel')}
