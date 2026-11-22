from .store import connect,source,at,repeated

def ecu(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'ecu',file)
        if repeated(db,'ecu',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['vin'],)).fetchone():raise ValueError('unknown_vehicle')
        db.execute('INSERT INTO ecu VALUES(?,?,?,?,?,?,?)',(d['id'],d['vin'],d['role'],d['serial'],d['version'],d['boot_counter'],sid))
        return {'id':d['id'],'replayed':False}
def package(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'software_package',file)
        if repeated(db,'software_package',d['id'],sid):return {'id':d['id'],'replayed':True}
        db.execute('INSERT INTO software_package VALUES(?,?,?,?,?,?)',(d['id'],d['role'],d['version'],d['digest'],d['minimum_peer'],sid))
        return {'id':d['id'],'replayed':False}
def receipt(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'install_receipt',file)
        if repeated(db,'install_receipt',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM ecu WHERE id=?',(d['ecu_id'],)).fetchone():raise ValueError('unknown_ecu')
        if not db.execute('SELECT 1 FROM software_package WHERE id=?',(d['package_id'],)).fetchone():raise ValueError('unknown_package')
        db.execute('INSERT INTO install_receipt VALUES(?,?,?,?,?,?,?)',(d['id'],d['ecu_id'],d['package_id'],d['phase'],d['boot_counter'],at(d['observed_at']),sid))
        return {'id':d['id'],'replayed':False}
def entitlement(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'feature_entitlement',file)
        if repeated(db,'feature_entitlement',d['id'],sid):return {'id':d['id'],'replayed':True}
        db.execute('INSERT INTO feature_entitlement VALUES(?,?,?,?,?,?)',(d['id'],d['vin'],d['feature'],at(d['valid_from']),at(d['valid_to']),sid))
        return {'id':d['id'],'replayed':False}
def captures(path):
    with connect(path) as db:return {t:[dict(x) for x in db.execute(f'SELECT * FROM {t} ORDER BY id')] for t in ('ecu','software_package','install_receipt','feature_entitlement')}
