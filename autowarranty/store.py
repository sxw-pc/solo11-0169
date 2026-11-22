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
CREATE TABLE IF NOT EXISTS emission_run(id TEXT PRIMARY KEY,vin TEXT,phase TEXT,started_at TEXT,ended_at TEXT,dyno_load_kw INTEGER,exhaust_lpm INTEGER,dilution_factor REAL,source_id INTEGER);
CREATE TABLE IF NOT EXISTS gas_sample(id TEXT PRIMARY KEY,run_id TEXT,compound TEXT,ppm REAL,analyzer TEXT,sampled_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS emission_series(id TEXT PRIMARY KEY,run_id TEXT,channel TEXT,hz REAL,unit TEXT,analyzer TEXT,points TEXT,clipped TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS analyzer_kernel(id TEXT PRIMARY KEY,analyzer TEXT,delay_min_s REAL,delay_max_s REAL,tau_s REAL,source_id INTEGER);
CREATE TABLE IF NOT EXISTS clock_sync(id TEXT PRIMARY KEY,run_id TEXT,analyzer TEXT,offset_s REAL,drift_ppm REAL,source_id INTEGER);
CREATE TABLE IF NOT EXISTS dilution_air(id TEXT PRIMARY KEY,run_id TEXT,lpm REAL,source_id INTEGER);
CREATE TABLE IF NOT EXISTS calibration(id TEXT PRIMARY KEY,analyzer TEXT,method TEXT,measured_delay_s REAL,at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS emission_cert(id TEXT PRIMARY KEY,run_id TEXT,scope TEXT,status TEXT,peak_ppm REAL,mass_min REAL,mass_max REAL,threshold REAL,reason TEXT,calibration_id TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS cert_event(id INTEGER PRIMARY KEY,cert_id TEXT,action TEXT,reason TEXT,at TEXT);
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
            for t in ('vehicle','diagnosis','authorization','part','repair','inspection','warranty','return_case','liability','emission_run','gas_sample',
                      'emission_series','analyzer_kernel','clock_sync','dilution_air','calibration','emission_cert','cert_event')}
