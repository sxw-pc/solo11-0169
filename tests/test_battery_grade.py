import json,os,subprocess,sys
from pathlib import Path
import pytest
from autowarranty import diagnosis,repair,battery,battery_grade
from autowarranty.battery_grade import span_ah
from autowarranty.report import report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def write(tmp,name,doc):
    p=tmp/name;p.write_text(json.dumps(doc),encoding='utf-8');return p

def base(tmp):
    db=tmp/'state.sqlite'
    for func,name in [(diagnosis.vehicle,'vehicle-ok.json'),(battery.pack,'battery-pack-ok.json'),
                      (battery.charge,'charge-test-ok.json'),(battery.resistance,'resistance-test-ok.json'),
                      (battery_grade.composition,'battery-composition-ok.json'),
                      (battery_grade.agreement,'use-agreement-window.json'),(battery_grade.agreement,'use-agreement-mobile.json'),
                      (battery_grade.window_evidence,'window-evidence-ok.json'),
                      (battery_grade.order,'use-order-window.json'),(battery_grade.order,'use-order-mobile.json')]:func(db,FIX/name)
    return db

def curve(end_low,end_high):
    """累计 Ah 曲线:[20,60] 窗口内每段增量与任何同族曲线相同,未测两端不同。"""
    return [[0,0.0],[20,end_low],[30,end_low+9.5],[40,end_low+19.0],[50,end_low+28.5],[60,end_low+38.0],
            [80,end_low+38.0+end_high/2],[100,end_low+38.0+end_high]]

JIA=curve(15.0,42.0)   # 甲:未测两端合计 57Ah,满行程 95Ah
YI=curve(10.0,28.0)    # 乙:两端电芯提前截止,未测两端合计 38Ah,满行程 76Ah

def full_cycle_doc(fid,c,weakest):
    ah=span_ah(c,0,100)
    return {'id':fid,'pack_id':'BAT1','composition':'CELL-96S-A','calibration':'CAL-2026-04',
            'start_soc':100,'end_soc':0,'amp_hours':ah,'weakest_cell_ah':weakest,
            'min_cell_mv':2500,'rest_mv':2900,'temp_c':25,
            'coulomb_bms_ah':ah,'coulomb_independent_ah':ah+0.2,
            'started_at':'2026-06-01T08:00:00Z','ended_at':'2026-06-01T13:00:00Z'}

def increments(c,lo,hi):
    pts=[(s,a) for s,a in c if lo<=s<=hi]
    return [round(b-a,6) for (_,a),(_,b) in zip(pts,pts[1:])]

def test_two_curves_share_ct1_window_but_differ_in_untested_ends():
    assert span_ah(JIA,20,60)==span_ah(YI,20,60)==38.0
    assert increments(JIA,20,60)==increments(YI,20,60)
    assert span_ah(JIA,0,20)+span_ah(JIA,60,100)==57.0
    assert span_ah(YI,0,20)+span_ah(YI,60,100)==38.0
    assert span_ah(JIA,0,100)==95.0 and span_ah(YI,0,100)==76.0
    naive=span_ah(JIA,20,60)/(60-20)*100
    assert naive==95.0 and naive!=span_ah(YI,0,100)

def test_delivered_vehicle_has_originals_but_no_use_certificate(tmp_path):
    db=tmp_path/'state.sqlite'
    for func,name in [(diagnosis.vehicle,'vehicle-ok.json'),(diagnosis.diagnose,'diagnosis-ok.json'),
                      (diagnosis.authorize,'authorization-ok.json'),(repair.part,'part-ok.json'),
                      (repair.execute,'repair-ok.json'),(repair.inspect,'inspection-ok.json'),
                      (battery.pack,'battery-pack-ok.json'),(battery.charge,'charge-test-ok.json'),
                      (battery.resistance,'resistance-test-ok.json')]:func(db,FIX/name)
    assert report(db)['state']['vehicle'][0]['status']=='delivered'
    originals=battery.tests(db)
    assert originals['charge'][0]['amp_hours']==38.0 and originals['resistance'][0]['milliohms']==52.0
    assert battery_grade.certificates(db,'BAT1')=={'certificates':[],'orders':[],'timeline':[]}
    assert audit(db)['replay_ok']

def test_window_certificate_issued_mobile_suspended(tmp_path):
    db=base(tmp_path)
    ev=battery_grade.evaluate(db,'BAT1')
    assert ev['proven_windows']==[{'window':[20,60],'amp_hours':38.0,'evidence':'WE1','charge_test':'CT1'}]
    assert ev['proven_window_ah']==38.0
    full=ev['full_range']
    assert full['status']=='unproven' and full['possible_ah']=={'min':38.0,'max':100.0}
    assert full['naive_extrapolation_ah']==95.0 and full['extrapolation']=='rejected'
    assert full['possible_ah']['min']<=76.0 and full['possible_ah']['max']>=95.0
    assert 'cutoff_voltage_unscanned' in full['reasons'] and 'untested_end_segments' in full['reasons']
    by_id={a['id']:a for a in ev['agreements']}
    assert by_id['AGR-W1']['eligible'] and by_id['AGR-W1']['certifiable_ah']==38.0
    assert by_id['AGR-M1']['eligible'] is False and by_id['AGR-M1']['action']=='suspend_and_request_full_cycle'
    plan={tuple(p['window']):p for p in ev['supplementary_plan']}
    assert plan[(20,60)]['discriminates'] is False
    assert plan[(0,20)]['discriminates'] and plan[(60,100)]['discriminates']
    assert plan[(0,100)]['kind']=='controlled_full_cycle'
    cert=battery_grade.certify(db,FIX/'certificate-window.json')
    assert cert['scope']=='window' and cert['certified_ah']==38.0
    with pytest.raises(ValueError,match='full_range_unproven'):
        battery_grade.certify(db,write(tmp_path,'certm.json',{'id':'CERT-M1','pack_id':'BAT1','agreement_id':'AGR-M1','at':'2026-05-04T00:00:00Z'}))
    shipped=battery_grade.ship(db,FIX/'shipment-window.json')
    assert shipped['status']=='shipped' and '38.0' in shipped['label'] and '仅限' in shipped['label']
    with pytest.raises(ValueError,match='certificate_missing'):
        battery_grade.ship(db,write(tmp_path,'shpm.json',{'id':'SHP-M1','order_id':'ORD-M1','at':'2026-05-05T00:00:00Z'}))
    assert audit(db)['replay_ok']

def test_late_full_test_95_upgrades_certificate_without_backdating(tmp_path):
    db=base(tmp_path)
    battery_grade.certify(db,FIX/'certificate-window.json')
    battery_grade.ship(db,FIX/'shipment-window.json')
    battery_grade.full_cycle(db,write(tmp_path,'fc95.json',full_cycle_doc('FC-A',JIA,weakest=93.0)))
    with pytest.raises(ValueError,match='backdated_certificate'):
        battery_grade.certify(db,write(tmp_path,'backdate.json',{'id':'CERT-M1','pack_id':'BAT1','agreement_id':'AGR-M1','at':'2026-05-04T00:00:00Z'}))
    upgraded=battery_grade.certify(db,write(tmp_path,'certm.json',{'id':'CERT-M1','pack_id':'BAT1','agreement_id':'AGR-M1','at':'2026-06-03T00:00:00Z'}))
    assert upgraded['scope']=='full_range' and upgraded['certified_ah']==93.0
    battery_grade.ship(db,write(tmp_path,'shpm.json',{'id':'SHP-M1','order_id':'ORD-M1','at':'2026-06-04T00:00:00Z'}))
    tl=battery_grade.certificates(db,'BAT1')
    certs={c['id']:c for c in tl['certificates']}
    assert certs['CERT-W1']['issued_at']=='2026-05-04T00:00:00Z' and certs['CERT-W1']['status']=='issued'
    assert certs['CERT-M1']['issued_at']=='2026-06-03T00:00:00Z'
    orders={o['id']:o for o in tl['orders']}
    assert orders['ORD-W1']['status']=='shipped' and orders['ORD-W1']['closed_at']=='2026-05-05T00:00:00Z'
    assert orders['ORD-M1']['status']=='shipped'
    assert [e['kind'] for e in tl['timeline']]==['certificate_issued','order_shipped','certificate_issued','order_shipped']
    assert audit(db)['replay_ok']

def test_late_full_test_76_revokes_mobile_order_keeps_window_delivery(tmp_path):
    db=base(tmp_path)
    battery_grade.certify(db,FIX/'certificate-window.json')
    battery_grade.ship(db,FIX/'shipment-window.json')
    battery_grade.full_cycle(db,write(tmp_path,'fc76.json',full_cycle_doc('FC-B',YI,weakest=75.0)))
    dec=battery_grade.decide(db,'BAT1','2026-06-02T00:00:00Z')
    assert dec['revoked_now']==['ORD-M1']
    assert dec['full_range']['status']=='proven' and dec['full_range']['measured_ah']==76.0
    with pytest.raises(ValueError,match='weakest_cell_below_requirement'):
        battery_grade.certify(db,write(tmp_path,'certm.json',{'id':'CERT-M1','pack_id':'BAT1','agreement_id':'AGR-M1','at':'2026-06-03T00:00:00Z'}))
    tl=battery_grade.certificates(db,'BAT1')
    orders={o['id']:o for o in tl['orders']}
    assert orders['ORD-M1']['status']=='revoked' and orders['ORD-M1']['closed_at']=='2026-06-02T00:00:00Z'
    assert orders['ORD-W1']['status']=='shipped' and orders['ORD-W1']['closed_at']=='2026-05-05T00:00:00Z'
    assert [e['kind'] for e in tl['timeline']]==['certificate_issued','order_shipped','order_revoked']
    assert audit(db)['replay_ok']

def test_weakest_cell_binds_mobile_certificate(tmp_path):
    db=base(tmp_path)
    battery_grade.full_cycle(db,write(tmp_path,'fc.json',full_cycle_doc('FC-W',JIA,weakest=78.0)))
    ev=battery_grade.evaluate(db,'BAT1')
    mobile={a['id']:a for a in ev['agreements']}['AGR-M1']
    assert mobile['eligible'] is False and mobile['reason']=='weakest_cell_below_requirement'
    assert mobile['weakest_cell_ah']==78.0
    with pytest.raises(ValueError,match='weakest_cell_below_requirement'):
        battery_grade.certify(db,write(tmp_path,'certm.json',{'id':'CERT-M1','pack_id':'BAT1','agreement_id':'AGR-M1','at':'2026-06-03T00:00:00Z'}))
    assert battery_grade.decide(db,'BAT1','2026-06-02T00:00:00Z')['revoked_now']==['ORD-M1']
    assert audit(db)['replay_ok']

def test_conflicting_coulomb_counters_quarantine_test(tmp_path):
    db=tmp_path/'state.sqlite'
    for func,name in [(diagnosis.vehicle,'vehicle-ok.json'),(battery.pack,'battery-pack-ok.json'),
                      (battery.charge,'charge-test-ok.json'),(battery_grade.composition,'battery-composition-ok.json'),
                      (battery_grade.agreement,'use-agreement-window.json')]:func(db,FIX/name)
    bad=json.loads((FIX/'window-evidence-ok.json').read_text());bad['coulomb_independent_ah']=39.5
    result=battery_grade.window_evidence(db,write(tmp_path,'we.json',bad))
    assert result['status']=='quarantined' and result['reason']=='coulomb_conflict'
    ev=battery_grade.evaluate(db,'BAT1')
    assert ev['proven_window_ah']==0.0 and ev['full_range']['possible_ah']=={'min':0.0,'max':100.0}
    assert 'coulomb_conflict_quarantined' in ev['full_range']['reasons']
    agr=ev['agreements'][0]
    assert agr['eligible'] is False and agr['reason']=='coulomb_conflict_quarantined'
    assert any(p.get('quarantined')=='WE1' for p in ev['supplementary_plan'])
    with pytest.raises(ValueError,match='window_unproven'):
        battery_grade.certify(db,write(tmp_path,'cert.json',{'id':'CERT-W1','pack_id':'BAT1','agreement_id':'AGR-W1','at':'2026-05-04T00:00:00Z'}))
    assert audit(db)['replay_ok']

def test_sensor_drift_quarantines_window(tmp_path):
    db=tmp_path/'state.sqlite'
    for func,name in [(diagnosis.vehicle,'vehicle-ok.json'),(battery.pack,'battery-pack-ok.json'),
                      (battery.charge,'charge-test-ok.json'),(battery_grade.composition,'battery-composition-ok.json')]:func(db,FIX/name)
    drifted=json.loads((FIX/'window-evidence-ok.json').read_text())
    drifted['soc_trace']=[[20,0.0],[30,9.0],[40,18.0],[50,27.0],[60,36.0]]
    result=battery_grade.window_evidence(db,write(tmp_path,'we.json',drifted))
    assert result['status']=='quarantined' and result['reason']=='sensor_drift'
    assert 'sensor_drift_quarantined' in battery_grade.evaluate(db,'BAT1')['full_range']['reasons']
    assert audit(db)['replay_ok']

def test_partial_cycle_and_unreached_cutoff_cannot_certify_full_range(tmp_path):
    db=base(tmp_path)
    partial=full_cycle_doc('FC-P',JIA,weakest=93.0);partial.update(start_soc=20,end_soc=100)
    result=battery_grade.full_cycle(db,write(tmp_path,'partial.json',partial))
    assert result['status']=='partial' and result['reason']=='partial_cycle_gap'
    cutoff=full_cycle_doc('FC-C',JIA,weakest=93.0);cutoff['min_cell_mv']=2900
    result=battery_grade.full_cycle(db,write(tmp_path,'cutoff.json',cutoff))
    assert result['status']=='partial' and result['reason']=='cutoff_voltage_unreached'
    ev=battery_grade.evaluate(db,'BAT1')
    assert ev['full_range']['status']=='unproven'
    assert 'partial_cycle_gap' in ev['full_range']['reasons'] and 'cutoff_voltage_unreached' in ev['full_range']['reasons']
    with pytest.raises(ValueError,match='full_range_unproven'):
        battery_grade.certify(db,write(tmp_path,'certm.json',{'id':'CERT-M1','pack_id':'BAT1','agreement_id':'AGR-M1','at':'2026-06-03T00:00:00Z'}))
    assert audit(db)['replay_ok']

def test_reassembly_forbids_splicing_previous_pack_tests(tmp_path):
    db=base(tmp_path)
    battery_grade.certify(db,FIX/'certificate-window.json')
    battery_grade.ship(db,FIX/'shipment-window.json')
    result=battery_grade.composition(db,write(tmp_path,'re.json',{'id':'COMP2','pack_id':'BAT1','composition':'CELL-96S-B','calibration':'CAL-2026-06','at':'2026-06-10T00:00:00Z'}))
    assert result['generation']==2 and result['superseded']==['CERT-W1'] and result['revoked_orders']==['ORD-M1']
    with pytest.raises(ValueError,match='identity_unchanged'):
        battery_grade.composition(db,write(tmp_path,'same.json',{'id':'COMP3','pack_id':'BAT1','composition':'CELL-96S-B','calibration':'CAL-2026-06','at':'2026-06-12T00:00:00Z'}))
    stale=json.loads((FIX/'window-evidence-ok.json').read_text());stale['id']='WE2'
    with pytest.raises(ValueError,match='composition_mismatch'):
        battery_grade.window_evidence(db,write(tmp_path,'we2.json',stale))
    spliced={**stale,'id':'WE3','composition':'CELL-96S-B','calibration':'CAL-2026-06','at':'2026-06-11T00:00:00Z'}
    with pytest.raises(ValueError,match='stale_test_identity'):
        battery_grade.window_evidence(db,write(tmp_path,'we3.json',spliced))
    ev=battery_grade.evaluate(db,'BAT1')
    assert ev['generation']==2 and ev['proven_windows']==[] and ev['full_range']['status']=='unproven'
    tl=battery_grade.certificates(db,'BAT1')
    certs={c['id']:c for c in tl['certificates']}
    assert certs['CERT-W1']['status']=='superseded'
    orders={o['id']:o for o in tl['orders']}
    assert orders['ORD-W1']['status']=='shipped' and orders['ORD-M1']['status']=='revoked'
    assert audit(db)['replay_ok']

def test_cli_battery_grade_and_certificates(tmp_path):
    env={**os.environ,'PYTHONPATH':str(FIX.parent)}
    db=tmp_path/'cli.sqlite'
    p=subprocess.run([sys.executable,'-m','autowarranty','demo','--db',str(db)],env=env,capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    out=json.loads(p.stdout)
    assert out['audit']['replay_ok'] and out['battery_grade']['proven_window_ah']==38.0
    g=subprocess.run([sys.executable,'-m','autowarranty','battery-grade','--db',str(db),'--pack','BAT1'],env=env,capture_output=True,text=True)
    assert g.returncode==0,g.stderr
    ev=json.loads(g.stdout)
    mobile={a['id']:a for a in ev['agreements']}['AGR-M1']
    assert ev['full_range']['status']=='unproven' and mobile['action']=='suspend_and_request_full_cycle'
    c=subprocess.run([sys.executable,'-m','autowarranty','battery-certificates','--db',str(db),'--pack','BAT1'],env=env,capture_output=True,text=True)
    assert c.returncode==0,c.stderr
    certs=json.loads(c.stdout)
    assert certs['certificates'][0]['id']=='CERT-W1' and certs['certificates'][0]['certified_ah']==38.0
    orders={o['id']:o for o in certs['orders']}
    assert orders['ORD-W1']['status']=='shipped' and orders['ORD-M1']['status']=='pending'
