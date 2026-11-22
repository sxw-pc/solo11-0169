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
CREATE TABLE IF NOT EXISTS ecu(id TEXT PRIMARY KEY,vin TEXT,role TEXT,serial TEXT,version TEXT,boot_counter INTEGER,source_id INTEGER);
CREATE TABLE IF NOT EXISTS software_package(id TEXT PRIMARY KEY,role TEXT,version TEXT,digest TEXT,minimum_peer TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS install_receipt(id TEXT PRIMARY KEY,ecu_id TEXT,package_id TEXT,phase TEXT,boot_counter INTEGER,observed_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS feature_entitlement(id TEXT PRIMARY KEY,vin TEXT,feature TEXT,valid_from TEXT,valid_to TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS trial_assignment(id TEXT PRIMARY KEY,vin TEXT,arm TEXT,package_id TEXT,assigned_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS drive_session(id TEXT PRIMARY KEY,vin TEXT,started_at TEXT,ended_at TEXT,distance_km REAL,route_class TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS safety_alert(id TEXT PRIMARY KEY,session_id TEXT,kind TEXT,occurred_at TEXT,severity INTEGER,source_id INTEGER);
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
            for t in ('vehicle','diagnosis','authorization','part','repair','inspection','warranty','return_case','liability','ecu','software_package','install_receipt','feature_entitlement','trial_assignment','drive_session','safety_alert')}
