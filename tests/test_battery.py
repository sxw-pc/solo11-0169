from pathlib import Path
from tempfile import TemporaryDirectory
from autowarranty import diagnosis,battery
from autowarranty.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def test_battery_capture_and_replay():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'state.sqlite'
        diagnosis.vehicle(db,FIX/'vehicle-ok.json')
        battery.pack(db,FIX/'battery-pack-ok.json')
        battery.charge(db,FIX/'charge-test-ok.json')
        battery.resistance(db,FIX/'resistance-test-ok.json')
        assert battery.tests(db)['charge'][0]['amp_hours']==38.0
        assert audit(db)['replay_ok']

def test_battery_duplicate():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'state.sqlite'
        diagnosis.vehicle(db,FIX/'vehicle-ok.json')
        battery.pack(db,FIX/'battery-pack-ok.json')
        assert battery.pack(db,FIX/'battery-pack-ok.json')['replayed']
