from pathlib import Path
from autowarranty import diagnosis,loaner
from autowarranty.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def test_loaner_capture_and_replay(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    loaner.asset(db,FIX/'loaner-ok.json')
    loaner.agreement(db,FIX/'loan-agreement-ok.json')
    loaner.fix(db,FIX/'trip-fix-ok.json')
    loaner.toll(db,FIX/'toll-receipt-ok.json')
    assert loaner.trips(db)['tolls'][0]['cents']==500
    assert audit(db)['replay_ok']

def test_loaner_duplicate(tmp_path):
    db=tmp_path/'state.sqlite'
    loaner.asset(db,FIX/'loaner-ok.json')
    assert loaner.asset(db,FIX/'loaner-ok.json')['replayed']
