from pathlib import Path
from autowarranty import diagnosis,title
from autowarranty.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def test_title_capture_and_replay(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    title.record(db,FIX/'title-ok.json')
    title.lien(db,FIX/'lien-ok.json')
    title.scan(db,FIX/'frame-scan-ok.json')
    assert title.records(db)['liens'][0]['amount_cents']==500000
    assert audit(db)['replay_ok']

def test_title_duplicate(tmp_path):
    db=tmp_path/'state.sqlite'
    title.record(db,FIX/'title-ok.json')
    assert title.record(db,FIX/'title-ok.json')['replayed']
