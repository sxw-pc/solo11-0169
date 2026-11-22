from pathlib import Path
from autowarranty import diagnosis,adas
from autowarranty.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def test_adas_capture_and_replay(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    adas.camera(db,FIX/'camera-ok.json')
    adas.board(db,FIX/'target-board-ok.json')
    adas.point(db,FIX/'image-point-ok.json')
    adas.pose(db,FIX/'road-pose-ok.json')
    assert adas.observations(db)['image_point'][0]['u_px']==200.0
    assert audit(db)['replay_ok']

def test_adas_duplicate(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    adas.camera(db,FIX/'camera-ok.json')
    assert adas.camera(db,FIX/'camera-ok.json')['replayed']
