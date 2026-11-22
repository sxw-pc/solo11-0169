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
CREATE TABLE IF NOT EXISTS camera_unit(id TEXT PRIMARY KEY,vin TEXT,serial TEXT,lens_model TEXT,width_px INTEGER,height_px INTEGER,source_id INTEGER);
CREATE TABLE IF NOT EXISTS target_board(id TEXT PRIMARY KEY,board_serial TEXT,points_json TEXT,ground_plane INTEGER,verification INTEGER,pitch_mrad REAL DEFAULT 0,survey_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS image_point(id TEXT PRIMARY KEY,camera_id TEXT,board_id TEXT,corner_no INTEGER,u_px REAL,v_px REAL,captured_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS road_pose(id TEXT PRIMARY KEY,camera_id TEXT,speed_kph INTEGER,pitch_mdeg INTEGER,load_kg INTEGER,observed_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS camera_mount(camera_id TEXT PRIMARY KEY,mount_generation INTEGER,serial TEXT,lens_model TEXT,updated_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS calibration(id TEXT PRIMARY KEY,camera_id TEXT,vin TEXT,serial TEXT,lens_model TEXT,mount_generation INTEGER,supersedes_id TEXT,lineage_note TEXT,params_json TEXT,observability_json TEXT,bounds_json TEXT,basis_json TEXT,next_target_json TEXT,summary_json TEXT,status TEXT,created_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS activation(id TEXT PRIMARY KEY,calibration_id TEXT,camera_id TEXT,profile TEXT,decision TEXT,steering_control INTEGER,restrictions_json TEXT,reasons_json TEXT,status TEXT,created_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS withdrawal_task(id TEXT PRIMARY KEY,calibration_id TEXT,activation_id TEXT,camera_id TEXT,reason TEXT,status TEXT,created_at TEXT,executed_at TEXT,source_id INTEGER);
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
            for t in ('vehicle','diagnosis','authorization','part','repair','inspection','warranty','return_case','liability','camera_unit','target_board','image_point','road_pose','camera_mount','calibration','activation','withdrawal_task')}
