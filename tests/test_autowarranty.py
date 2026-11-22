import json,os,subprocess,sys
from pathlib import Path
import pytest
from autowarranty import diagnosis,repair,warranty
from autowarranty.report import report,audit

FIX=Path(__file__).parent.parent/'fixtures'
def write(tmp,name,doc):
    p=tmp/name;p.write_text(json.dumps(doc),encoding='utf-8');return p
def authorized(tmp):
    db=tmp/'state.sqlite'
    for func,name in [(diagnosis.vehicle,'vehicle-ok.json'),(diagnosis.diagnose,'diagnosis-ok.json'),
                      (diagnosis.authorize,'authorization-ok.json'),(repair.part,'part-ok.json')]:func(db,FIX/name)
    return db
def delivered(tmp):
    db=authorized(tmp);repair.execute(db,FIX/'repair-ok.json');repair.inspect(db,FIX/'inspection-ok.json');warranty.grant(db,FIX/'warranty-ok.json');return db

def test_authorization_repair_delivery_and_warranty(tmp_path):
    print(__import__('autowarranty').__file__)
    db=delivered(tmp_path);warranty.return_case(db,FIX/'return-ok.json')
    result=warranty.decide(db,'RET1','2026-06-20T00:00:00Z')
    assert (result['status'],result['responsible_party'])==('covered','repair_shop')
    assert report(db)['state']['vehicle'][0]['status']=='delivered'
    assert audit(db)['replay_ok']

def test_wrong_diagnostic_vin_rolls_back_source(tmp_path):
    db=tmp_path/'state.sqlite';diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    with pytest.raises(ValueError,match='diagnostic_vin_mismatch'):diagnosis.diagnose(db,FIX/'diagnosis-bad.json')
    assert report(db)['state']['diagnosis']==[]

def test_unapproved_work_and_cost_overrun_block_execution(tmp_path):
    db=authorized(tmp_path)
    changed=json.loads((FIX/'repair-ok.json').read_text());changed.update(id='R2',work_code='engine')
    with pytest.raises(ValueError,match='work_not_authorized'):repair.execute(db,write(tmp_path,'scope.json',changed))
    changed.update(id='R3',work_code='brake',cost='501.00')
    with pytest.raises(ValueError,match='authorization_expired_or_over_budget'):repair.execute(db,write(tmp_path,'cost.json',changed))
    assert report(db)['state']['repair']==[]

def test_recalled_or_wrong_model_part_blocks_delivery(tmp_path):
    db=authorized(tmp_path);repair.part(db,FIX/'part-bad.json')
    changed=json.loads((FIX/'repair-ok.json').read_text());changed.update(id='R2',part_id='PART2')
    with pytest.raises(ValueError,match='part_incompatible_or_recalled'):repair.execute(db,write(tmp_path,'part2.json',changed))
    assert report(db)['state']['vehicle'][0]['status']=='registered'

def test_independent_road_test_controls_delivery(tmp_path):
    db=authorized(tmp_path);repair.execute(db,FIX/'repair-ok.json')
    same=json.loads((FIX/'inspection-ok.json').read_text());same['inspector']='TECH1'
    with pytest.raises(ValueError,match='independent_inspector_required'):repair.inspect(db,write(tmp_path,'same.json',same))
    fail={**same,'id':'I2','inspector':'INSPECTOR2','road_test_ok':False}
    assert repair.inspect(db,write(tmp_path,'fail.json',fail))['status']=='rework'
    assert report(db)['state']['vehicle'][0]['status']=='repairing'

def test_different_symptom_and_expired_window_not_covered(tmp_path):
    db=delivered(tmp_path);warranty.return_case(db,FIX/'return-bad.json')
    assert warranty.decide(db,'RET2','2026-06-20T00:00:00Z')['reason']=='different_symptom'
    late=json.loads((FIX/'return-ok.json').read_text());late.update(id='RET3',occurred_at='2026-10-01T00:00:00Z')
    warranty.return_case(db,write(tmp_path,'late.json',late))
    assert warranty.decide(db,'RET3','2026-10-02T00:00:00Z')['reason']=='outside_warranty'
    assert audit(db)['replay_ok']

def test_late_part_recall_shifts_responsibility_to_supplier(tmp_path):
    db=delivered(tmp_path);warranty.return_case(db,FIX/'return-ok.json')
    before=warranty.decide(db,'RET1','2026-06-20T00:00:00Z')
    assert before['responsible_party']=='repair_shop'
    assert warranty.recall_part(db,'PART1')['impacted_repairs']==['R1']
    after=warranty.decide(db,'RET1','2026-06-21T00:00:00Z')
    assert after['responsible_party']=='supplier'
    assert report(db)['state']['liability'][0]['responsible_party']=='repair_shop'
    assert audit(db)['replay_ok']

def test_source_id_conflict_and_replay(tmp_path):
    db=authorized(tmp_path)
    assert repair.execute(db,FIX/'repair-ok.json')['status']=='performed'
    assert repair.execute(db,FIX/'repair-ok.json')['replayed']
    changed=json.loads((FIX/'repair-ok.json').read_text());changed['cost']='400.00'
    with pytest.raises(ValueError,match='event_id_conflict'):repair.execute(db,write(tmp_path,'changed.json',changed))
    assert report(db)['state']['repair'][0]['cost_cents']==45000

def test_public_cli_and_local_module(tmp_path):
    env={**os.environ,'PYTHONPATH':str(Path(__file__).parent.parent)}
    p=subprocess.run([sys.executable,'-m','autowarranty','demo','--db',str(tmp_path/'cli.sqlite')],env=env,capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    out=json.loads(p.stdout);assert out['audit']['replay_ok'] and out['liability']['responsible_party']=='repair_shop'
    assert str(Path(__import__('autowarranty').__file__).resolve()).startswith(str(Path(__file__).parent.parent.resolve()))
