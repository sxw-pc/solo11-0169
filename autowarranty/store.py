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
CREATE TABLE IF NOT EXISTS loaner(id TEXT PRIMARY KEY,plate TEXT,model TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS loan_agreement(id TEXT PRIMARY KEY,customer_vin TEXT,loaner_id TEXT,borrower TEXT,started_at TEXT,due_at TEXT,plan TEXT,version INTEGER,daily_cents INTEGER,supersedes TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS trip_fix(id TEXT PRIMARY KEY,agreement_id TEXT,observed_at TEXT,x_m INTEGER,y_m INTEGER,accuracy_m INTEGER,source_id INTEGER);
CREATE TABLE IF NOT EXISTS toll_receipt(id TEXT PRIMARY KEY,plate TEXT,gate TEXT,passed_at TEXT,cents INTEGER,source_id INTEGER);
CREATE TABLE IF NOT EXISTS fix_batch(id TEXT PRIMARY KEY,agreement_id TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS fix_batch_item(id TEXT PRIMARY KEY,batch_id TEXT,agreement_id TEXT,observed_at TEXT,x_m INTEGER,y_m INTEGER,accuracy_m INTEGER,source_id INTEGER);
CREATE TABLE IF NOT EXISTS road_network(id TEXT PRIMARY KEY,version INTEGER,nodes TEXT,edges TEXT,closures TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS tag_handover(id TEXT PRIMARY KEY,loaner_id TEXT,plate TEXT,tag TEXT,borrower TEXT,agreement_id TEXT,from_at TEXT,to_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS gate_signature(id TEXT PRIMARY KEY,gate TEXT,plate TEXT,tag TEXT,passed_at TEXT,signature TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS ocr_read(id TEXT PRIMARY KEY,receipt_id TEXT,plate_text TEXT,confidence REAL,signed INTEGER,image_sha TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS plate_appeal(id TEXT PRIMARY KEY,plate TEXT,reason TEXT,at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS loan_return(id TEXT PRIMARY KEY,agreement_id TEXT,returned_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS customer_payment(id TEXT PRIMARY KEY,agreement_id TEXT,receipt_id TEXT,cents INTEGER,at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS toll_payment(id TEXT PRIMARY KEY,receipt_id TEXT,kind TEXT,cents INTEGER,at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS toll_decision(row_id INTEGER PRIMARY KEY,subject_id TEXT,subject_kind TEXT,receipt_id TEXT,agreement_id TEXT,revision INTEGER,status TEXT,reason TEXT,customer_cents INTEGER,shop_verify_cents INTEGER,trust TEXT,path_support TEXT,candidate_paths TEXT,trajectory TEXT,identity_chain TEXT,required_evidence TEXT,days INTEGER,at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS toll_adjustment(row_id INTEGER PRIMARY KEY,agreement_id TEXT,subject_id TEXT,kind TEXT,cents INTEGER,reason TEXT,at TEXT,source_id INTEGER);
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
            for t in ('vehicle','diagnosis','authorization','part','repair','inspection','warranty','return_case','liability','loaner','loan_agreement','trip_fix','toll_receipt','fix_batch','fix_batch_item','road_network','tag_handover','gate_signature','ocr_read','plate_appeal','loan_return','customer_payment','toll_payment','toll_decision','toll_adjustment')}
