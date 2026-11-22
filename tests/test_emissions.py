from pathlib import Path
from tempfile import TemporaryDirectory
from autowarranty import diagnosis,emissions
from autowarranty.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def test_emission_capture_and_replay():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'state.sqlite'
        diagnosis.vehicle(db,FIX/'vehicle-ok.json')
        emissions.run(db,FIX/'emission-run-ok.json')
        emissions.sample(db,FIX/'gas-sample-ok.json')
        assert emissions.readings(db)['samples'][0]['ppm']==100
        assert audit(db)['replay_ok']

def test_emission_duplicate():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'state.sqlite'
        diagnosis.vehicle(db,FIX/'vehicle-ok.json')
        emissions.run(db,FIX/'emission-run-ok.json')
        assert emissions.run(db,FIX/'emission-run-ok.json')['replayed']
