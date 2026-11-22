from pathlib import Path
from autowarranty import diagnosis,ota
from autowarranty.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def test_ota_capture_and_replay(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    ota.ecu(db,FIX/'ecu-ok.json')
    ota.package(db,FIX/'software-package-ok.json')
    ota.receipt(db,FIX/'install-receipt-ok.json')
    ota.entitlement(db,FIX/'feature-entitlement-ok.json')
    assert ota.captures(db)['install_receipt'][0]['phase']=='written'
    assert audit(db)['replay_ok']

def test_ota_duplicate(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    ota.ecu(db,FIX/'ecu-ok.json')
    assert ota.ecu(db,FIX/'ecu-ok.json')['replayed']
