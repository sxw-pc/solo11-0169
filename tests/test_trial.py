from pathlib import Path
from autowarranty import diagnosis,trial
from autowarranty.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def test_trial_capture_and_replay(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    trial.assignment(db,FIX/'trial-assignment-ok.json')
    trial.session(db,FIX/'drive-session-ok.json')
    trial.alert(db,FIX/'safety-alert-ok.json')
    assert trial.captures(db)['safety_alert'][0]['kind']=='false_brake'
    assert audit(db)['replay_ok']

def test_trial_duplicate(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    trial.assignment(db,FIX/'trial-assignment-ok.json')
    assert trial.assignment(db,FIX/'trial-assignment-ok.json')['replayed']
