from .store import connect,source,repeated,at

def grant(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'warranty',file)
        if repeated(db,'warranty',d['id'],sid):return {'id':d['id'],'replayed':True}
        repair=db.execute('SELECT * FROM repair WHERE id=?',(d['repair_id'],)).fetchone()
        if not repair or repair['status']!='released':raise ValueError('repair_not_delivered')
        valid=at(d['valid_to'])
        if valid<=repair['at']:raise ValueError('invalid_warranty_window')
        diag=db.execute('SELECT symptom FROM diagnosis WHERE id=?',(repair['diagnosis_id'],)).fetchone()
        if diag['symptom']!=d['covered_symptom']:raise ValueError('warranty_scope_mismatch')
        db.execute('INSERT INTO warranty VALUES(?,?,?,?,?,?)',(d['id'],repair['id'],valid,d['covered_symptom'],'active',sid))
        return {'id':d['id'],'status':'active'}

def return_case(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'return_case',file)
        if repeated(db,'return_case',d['id'],sid):return {'id':d['id'],'replayed':True}
        repair=db.execute('SELECT * FROM repair WHERE id=?',(d['repair_id'],)).fetchone()
        if not repair or repair['vin']!=d['vin']:raise ValueError('return_vehicle_mismatch')
        occurred=at(d['occurred_at'])
        if occurred<=repair['at']:raise ValueError('return_before_repair')
        db.execute('INSERT INTO return_case VALUES(?,?,?,?,?,?,?)',(d['id'],repair['id'],d['vin'],d['symptom'],occurred,1 if d.get('external_damage') else 0,sid))
        return {'id':d['id'],'status':'reported'}

def decide(path,case_id,at_time):
    with connect(path) as db,db:
        c=db.execute('SELECT * FROM return_case WHERE id=?',(case_id,)).fetchone()
        if not c:raise ValueError('unknown_return_case')
        moment=at(at_time)
        import hashlib,json
        raw=json.dumps({'case_id':case_id,'at':moment},sort_keys=True).encode()
        digest=hashlib.sha256(raw).hexdigest()
        old_source=db.execute('SELECT 1 FROM source WHERE sha=?',(digest,)).fetchone()
        if old_source:
            prior=db.execute('SELECT * FROM liability WHERE case_id=? AND at=? ORDER BY id DESC LIMIT 1',(case_id,moment)).fetchone()
            if prior:return {'case_id':case_id,'status':prior['status'],'responsible_party':prior['responsible_party'],'reason':prior['reason'],'replayed':True}
        else:db.execute("INSERT INTO source(kind,sha,raw) VALUES('liability',?,?)",(digest,raw))
        w=db.execute('SELECT * FROM warranty WHERE repair_id=? ORDER BY valid_to DESC LIMIT 1',(c['repair_id'],)).fetchone()
        repair=db.execute('SELECT * FROM repair WHERE id=?',(c['repair_id'],)).fetchone()
        part=db.execute('SELECT * FROM part WHERE id=?',(repair['part_id'],)).fetchone()
        if not w or c['occurred_at']>w['valid_to']:status,party,reason='not_covered','owner','outside_warranty'
        elif c['symptom']!=w['covered_symptom']:status,party,reason='not_covered','owner','different_symptom'
        elif c['external_damage']:status,party,reason='disputed','undetermined','external_damage'
        elif part['recalled']:status,party,reason='covered','supplier','recalled_part'
        else:status,party,reason='covered','repair_shop','same_fault_in_warranty'
        db.execute('INSERT INTO liability(case_id,status,responsible_party,reason,at) VALUES(?,?,?,?,?)',(case_id,status,party,reason,moment))
        return {'case_id':case_id,'status':status,'responsible_party':party,'reason':reason}

def recall_part(path,part_id):
    with connect(path) as db,db:
        import hashlib,json
        raw=json.dumps({'part_id':part_id},sort_keys=True).encode()
        digest=hashlib.sha256(raw).hexdigest()
        part=db.execute('SELECT * FROM part WHERE id=?',(part_id,)).fetchone()
        if not part:raise ValueError('unknown_part')
        if not db.execute('SELECT 1 FROM source WHERE sha=?',(digest,)).fetchone():
            db.execute("INSERT INTO source(kind,sha,raw) VALUES('recall',?,?)",(digest,raw))
        db.execute("UPDATE part SET recalled=1,status='recalled' WHERE id=?",(part_id,))
        impacted=[x['id'] for x in db.execute('SELECT id FROM repair WHERE part_id=?',(part_id,))]
        return {'part_id':part_id,'impacted_repairs':impacted}
