from pathlib import Path
from autowarranty import diagnosis,paint
from autowarranty.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def test_paint_capture_and_replay(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    paint.sample(db,FIX/'paint-sample-ok.json')
    paint.pigment(db,FIX/'pigment-ok.json')
    paint.panel(db,FIX/'spray-panel-ok.json')
    assert paint.captures(db)['spray_panel'][0]['illuminant']=='D65'
    assert audit(db)['replay_ok']

def test_paint_duplicate(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    paint.sample(db,FIX/'paint-sample-ok.json')
    assert paint.sample(db,FIX/'paint-sample-ok.json')['replayed']
