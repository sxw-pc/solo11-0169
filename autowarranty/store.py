import sqlite3,hashlib,json
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime

SCHEMA='''PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS source(id INTEGER PRIMARY KEY,kind TEXT,sha TEXT UNIQUE,raw BLOB);
CREATE TABLE IF NOT EXISTS vehicle(vin TEXT PRIMARY KEY,owner TEXT,model TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS diagnosis(id TEXT PRIMARY KEY,vin TEXT,device_vin TEXT,symptom TEXT,work_code TEXT,at TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS authorization(id TEXT PRIMARY KEY,diagnosis_id TEXT,owner TEXT,work_code TEXT,max_cents INTEGER,valid_to TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS part(id TEXT PRIMARY KEY,model TEXT,work_code TEXT,batch TEXT,supplier TEXT,recalled INTEGER,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS repair(id TEXT PRIMARY KEY,vin TEXT,diagnosis_id TEXT,authorization_id TEXT,part_id TEXT,work_code TEXT,cost_cents INTEGER,technician TEXT,at TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS inspection(id TEXT PRIMARY KEY,repair_id TEXT,inspector TEXT,road_test_ok INTEGER,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS warranty(id TEXT PRIMARY KEY,repair_id TEXT,valid_to TEXT,covered_symptom TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS return_case(id TEXT PRIMARY KEY,repair_id TEXT,vin TEXT,symptom TEXT,occurred_at TEXT,external_damage INTEGER,source_id INTEGER);
CREATE TABLE IF NOT EXISTS liability(id INTEGER PRIMARY KEY,case_id TEXT,status TEXT,responsible_party TEXT,reason TEXT,at TEXT);
CREATE TABLE IF NOT EXISTS battery_pack(id TEXT PRIMARY KEY,vin TEXT,nominal_ah INTEGER,cell_count INTEGER,source_id INTEGER);
CREATE TABLE IF NOT EXISTS charge_test(id TEXT PRIMARY KEY,pack_id TEXT,start_soc INTEGER,end_soc INTEGER,amp_hours REAL,temp_c INTEGER,started_at TEXT,ended_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS resistance_test(id TEXT PRIMARY KEY,pack_id TEXT,milliohms REAL,temp_c INTEGER,measured_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS pack_composition(id TEXT PRIMARY KEY,pack_id TEXT,generation INTEGER,composition TEXT,calibration TEXT,at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS use_agreement(id TEXT PRIMARY KEY,pack_id TEXT,buyer TEXT,kind TEXT,window_start INTEGER,window_end INTEGER,min_full_ah REAL,at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS window_evidence(id TEXT PRIMARY KEY,pack_id TEXT,charge_test_id TEXT,composition TEXT,calibration TEXT,coulomb_bms_ah REAL,coulomb_independent_ah REAL,min_cell_mv INTEGER,rest_mv INTEGER,temp_c INTEGER,soc_trace TEXT,curve TEXT,at TEXT,status TEXT,detail TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS full_cycle_test(id TEXT PRIMARY KEY,pack_id TEXT,composition TEXT,calibration TEXT,start_soc INTEGER,end_soc INTEGER,amp_hours REAL,weakest_cell_ah REAL,min_cell_mv INTEGER,rest_mv INTEGER,temp_c INTEGER,coulomb_bms_ah REAL,coulomb_independent_ah REAL,started_at TEXT,ended_at TEXT,status TEXT,detail TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS use_certificate(id TEXT PRIMARY KEY,pack_id TEXT,agreement_id TEXT,scope TEXT,window_start INTEGER,window_end INTEGER,certified_ah REAL,status TEXT,issued_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS use_order(id TEXT PRIMARY KEY,pack_id TEXT,agreement_id TEXT,status TEXT,ordered_at TEXT,closed_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS grade_event(id INTEGER PRIMARY KEY,pack_id TEXT,kind TEXT,ref_id TEXT,at TEXT,detail TEXT);
'''
@contextmanager
def connect(path):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(p);db.row_factory=sqlite3.Row
    try:db.executescript(SCHEMA);yield db
    finally:db.close()
def source(db,kind,file):
    raw=Path(file).read_bytes();digest=hashlib.sha256(raw).hexdigest();doc=json.loads(raw)
    old=db.execute('SELECT id,kind FROM source WHERE sha=?',(digest,)).fetchone()
    if old:
        if old['kind']!=kind:raise ValueError('source_kind_conflict')
        return old['id'],doc,True
    return db.execute('INSERT INTO source(kind,sha,raw) VALUES(?,?,?)',(kind,digest,raw)).lastrowid,doc,False
def at(value):
    if not isinstance(value,str) or not value.endswith('Z'):raise ValueError('utc_required')
    datetime.fromisoformat(value[:-1]+'+00:00');return value
def money(value):
    from decimal import Decimal
    cents=Decimal(str(value))*100
    if cents<=0 or cents!=cents.to_integral_value():raise ValueError('invalid_money')
    return int(cents)
def repeated(db,table,key,sid):
    old=db.execute(f'SELECT source_id FROM {table} WHERE id=?',(key,)).fetchone()
    if old:
        if old['source_id']!=sid:raise ValueError('event_id_conflict')
        return True
    return False
def snapshot(db):
    return {t:[dict(x) for x in db.execute(f'SELECT * FROM {t} ORDER BY 1')]
            for t in ('vehicle','diagnosis','authorization','part','repair','inspection','warranty','return_case','liability','battery_pack','charge_test','resistance_test',
                      'pack_composition','use_agreement','window_evidence','full_cycle_test','use_certificate','use_order','grade_event')}
